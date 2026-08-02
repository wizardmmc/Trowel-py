"""把发布前的旧开发数据一次性迁入正式 Desktop 数据根。"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, IO

from trowel_py.memory.sessions_repo.database import initialize_schema
from trowel_py.memory.store.codec import _dump_frontmatter, _split_frontmatter
from trowel_py.resource_lifecycle.processes import LocalProcessController

_MIGRATION_VERSION = 1
_TITLE_SOURCE_RANK = {"native": 0, "prompt": 1, "generated": 2, "manual": 3}
_SESSION_TABLE_KEYS = {
    "sessions": ("cc_session_id",),
    "session_bindings": ("trowel_session_id",),
    "codex_turns": ("thread_id", "turn_id"),
    "session_review_requests": ("trowel_session_id",),
}
_LOCK_FILE_NAMES = {
    ".dictionary.lock",
    ".distill.lock",
    ".review.lock",
    ".suggestions.lock",
}


class DesktopDataMigrationError(RuntimeError):
    """表示迁移前置条件、来源结构或合并结果不满足安全门禁。"""


@dataclass(frozen=True)
class DesktopDataMigrationPlan:
    """保存一次只读迁移盘点和 apply 所需的确定路径。

    Attributes:
        target_root: 新正式业务数据根。
        legacy_root: 旧 browser/CLI 开发数据根。
        previous_app_root: 旧候选直接写入业务文件的 Electron userData 根。
        config_source: 用户显式选择的配置文件；未选择时为 None。
        blockers: 当前阻止 apply 的前置条件。
        estimated_bytes: 两个 Memory 来源的逻辑文件字节总数。
        legacy_notes: 旧长期 Memory 中的 Note 文件数。
        legacy_codex_turns: 旧 sessions registry 中的 Codex turn 数。
        current_codex_turns: 当前候选 sessions registry 中的 Codex turn 数。
        overlapping_codex_turns: 两个 registry 使用相同 thread/turn 主键的数量。
        migrate_old_garden: 固定为 False，声明不导入旧开发主数据库。
    """

    target_root: Path
    legacy_root: Path
    previous_app_root: Path
    config_source: Path | None
    blockers: tuple[str, ...]
    estimated_bytes: int
    legacy_notes: int
    legacy_codex_turns: int
    current_codex_turns: int
    overlapping_codex_turns: int
    migrate_old_garden: bool = False

    @property
    def ready(self) -> bool:
        """返回当前盘点是否允许执行 apply。"""

        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        """把路径和元数据转换成可打印的 JSON 对象。"""

        payload = asdict(self)
        for key in ("target_root", "legacy_root", "previous_app_root"):
            payload[key] = str(payload[key])
        payload["config_source"] = (
            str(self.config_source) if self.config_source is not None else None
        )
        payload["ready"] = self.ready
        return payload


@dataclass(frozen=True)
class DesktopDataMigrationResult:
    """描述已原子发布的一次迁移结果。"""

    status: str
    target_root: Path
    manifest_path: Path


def _tree_bytes(root: Path) -> int:
    """返回目录内普通文件的逻辑字节数，缺失目录按零处理。"""

    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _count_turns(memory_root: Path) -> int:
    """只读统计 sessions registry 中的 Codex turn 数。"""

    database = memory_root / "meta" / "sessions.db"
    if not database.exists():
        return 0
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='codex_turns'"
        ).fetchone()
        if table is None:
            return 0
        return int(connection.execute("SELECT COUNT(*) FROM codex_turns").fetchone()[0])
    finally:
        connection.close()


def _codex_turn_rows(memory_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """只读返回 Codex turn 主键、生命周期和 journal 路径。"""

    database = memory_root / "meta" / "sessions.db"
    if not database.exists():
        return {}
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='codex_turns'"
        ).fetchone()
        if table is None:
            return {}
        return {
            (row["thread_id"], row["turn_id"]): dict(row)
            for row in connection.execute("SELECT * FROM codex_turns")
        }
    finally:
        connection.close()


def _turn_lifecycle_rank(row: dict[str, Any]) -> int:
    """按 extracted、completed、running 的不可逆进度为 turn 排序。"""

    if row.get("extracted_at"):
        return 3
    if row.get("completed_at") or row.get("status") == "completed":
        return 2
    return 1


def _turn_overlap_audit(
    legacy_memory: Path,
    current_memory: Path,
) -> tuple[int, tuple[str, ...], set[Path]]:
    """返回重复 turn 数、阻塞原因和应保留旧版本的当前 journal 相对路径。"""

    legacy = _codex_turn_rows(legacy_memory)
    current = _codex_turn_rows(current_memory)
    overlap = set(legacy) & set(current)
    blockers: list[str] = []
    keep_legacy: set[Path] = set()
    for key in sorted(overlap):
        old_row = legacy[key]
        current_row = current[key]
        if _turn_lifecycle_rank(current_row) > _turn_lifecycle_rank(old_row):
            blockers.append(
                "current App has a more advanced overlapping Codex turn: "
                f"{key[0]} / {key[1]}"
            )
            continue
        journal = Path(str(current_row.get("journal_path") or ""))
        try:
            keep_legacy.add(journal.relative_to(current_memory))
        except ValueError:
            blockers.append(
                "current overlapping journal is outside its Memory root: "
                f"{journal}"
            )
    return len(overlap), tuple(blockers), keep_legacy


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """宽松读取一个 JSON 对象，缺失、损坏或非对象时返回 None。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _previous_app_shutdown_blockers(previous_app_root: Path) -> tuple[str, ...]:
    """核验上一候选已为当前实例写出干净退出事实且没有存活子进程。"""

    snapshot = _read_json_object(previous_app_root / "resource-lifecycle.json")
    exit_marker = _read_json_object(previous_app_root / "resource-exit.json")
    if (
        snapshot is None
        or snapshot.get("version") != 1
        or not isinstance(snapshot.get("app_instance_id"), str)
        or not snapshot["app_instance_id"]
        or not isinstance(snapshot.get("resources"), list)
    ):
        return (
            "previous App resource snapshot is missing or invalid; "
            "start and fully quit Trowel first",
        )

    clean_exit = (
        exit_marker is not None
        and exit_marker.get("version") == 1
        and exit_marker.get("app_instance_id") == snapshot["app_instance_id"]
        and exit_marker.get("status") == "closed"
        and exit_marker.get("remaining_resource_count") == 0
    )
    blockers: list[str] = []
    if not clean_exit:
        blockers.append(
            "previous App has no matching clean exit for its current instance; "
            "fully quit Trowel first"
        )

    controller = LocalProcessController()
    live = 0
    for resource in snapshot["resources"]:
        if not isinstance(resource, dict) or resource.get("state") == "closed":
            continue
        pid = resource.get("pid")
        process_group = resource.get("process_group")
        start_identity = resource.get("process_start_identity")
        if not isinstance(pid, int) or not isinstance(process_group, int):
            continue
        if not isinstance(start_identity, str) or not start_identity:
            continue
        current = controller.inspect(pid)
        if (
            current is not None
            and current.process_group == process_group
            and current.start_identity == start_identity
        ):
            live += 1
    if live:
        blockers.append(
            f"previous App still owns {live} live resource(s); quit Trowel first"
        )
    return tuple(blockers)


def _clean_previous_app_instance_id(previous_app_root: Path) -> str | None:
    """返回已由同一 exit marker 证明干净退出的上一候选实例摘要。"""

    snapshot = _read_json_object(previous_app_root / "resource-lifecycle.json")
    exit_marker = _read_json_object(previous_app_root / "resource-exit.json")
    if snapshot is None or exit_marker is None:
        return None
    instance_id = snapshot.get("app_instance_id")
    if (
        snapshot.get("version") == 1
        and isinstance(instance_id, str)
        and bool(instance_id)
        and exit_marker.get("version") == 1
        and exit_marker.get("app_instance_id") == instance_id
        and exit_marker.get("status") == "closed"
        and exit_marker.get("remaining_resource_count") == 0
    ):
        return instance_id
    return None


def plan_desktop_data_migration(
    *,
    target_root: Path,
    legacy_root: Path,
    previous_app_root: Path,
    config_source: Path | None = None,
) -> DesktopDataMigrationPlan:
    """只读盘点一次旧开发数据到正式 App 根的迁移。

    Args:
        target_root: 准备原子发布的新正式业务数据根。
        legacy_root: 包含旧长期 Memory/Profile 的 ``~/.trowel`` 等价路径。
        previous_app_root: 当前候选曾直接写入业务文件的 Electron userData 根。
        config_source: 用户显式选择导入的 ``config.toml``。

    Returns:
        带来源数量、逻辑大小和阻塞原因的迁移计划。
    """

    target = target_root.expanduser().resolve()
    legacy = legacy_root.expanduser().resolve()
    previous = previous_app_root.expanduser().resolve()
    config = config_source.expanduser().resolve() if config_source is not None else None
    legacy_memory = legacy / "memory"
    current_memory = previous / "memory"
    blockers: list[str] = []
    if target.exists() and any(target.iterdir()):
        blockers.append(f"target data root is not empty: {target}")
    if not legacy_memory.is_dir():
        blockers.append(f"legacy memory root is missing: {legacy_memory}")
    if not (previous / "trowel.db").is_file():
        blockers.append(f"previous App database is missing: {previous / 'trowel.db'}")
    if config is not None and not config.is_file():
        blockers.append(f"selected config is missing: {config}")
    symlink = next(
        (path for path in legacy_memory.rglob("*") if path.is_symlink()), None
    ) if legacy_memory.exists() else None
    if symlink is not None:
        blockers.append(f"legacy memory contains a symbolic link: {symlink}")
    blockers.extend(_previous_app_shutdown_blockers(previous))
    overlap_count, overlap_blockers, _ = _turn_overlap_audit(
        legacy_memory, current_memory
    )
    blockers.extend(overlap_blockers)
    notes_dir = legacy_memory / "notes"
    legacy_notes = (
        sum(1 for path in notes_dir.glob("*.md") if path.is_file())
        if notes_dir.is_dir()
        else 0
    )
    return DesktopDataMigrationPlan(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous,
        config_source=config,
        blockers=tuple(blockers),
        estimated_bytes=_tree_bytes(legacy_memory) + _tree_bytes(current_memory),
        legacy_notes=legacy_notes,
        legacy_codex_turns=_count_turns(legacy_memory),
        current_codex_turns=_count_turns(current_memory),
        overlapping_codex_turns=overlap_count,
    )


def _open_lock(path: Path) -> IO[str]:
    """以仅当前用户可读写的模式打开迁移锁文件。"""

    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    return os.fdopen(descriptor, "r+", encoding="utf-8")


@contextmanager
def _migration_lock(parent: Path) -> Iterator[None]:
    """防止两个迁移器同时构造和发布同一个 data 目录。"""

    parent.mkdir(parents=True, exist_ok=True)
    path = parent / ".data-migration.lock"
    handle = _open_lock(path)
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DesktopDataMigrationError("another data migration is running") from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _copy_sqlite(source: Path, destination: Path) -> None:
    """通过 SQLite Backup API 复制并验证一个一致数据库快照。"""

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{source.resolve().as_uri()}?mode=ro", uri=True
        )
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        integrity = tuple(
            str(row[0]) for row in destination_connection.execute("PRAGMA quick_check")
        )
        if integrity != ("ok",):
            raise DesktopDataMigrationError(
                f"SQLite snapshot failed integrity check: {source}"
            )
    except sqlite3.Error as exc:
        raise DesktopDataMigrationError(
            f"SQLite snapshot failed for {source}: {exc}"
        ) from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()


def _copy_memory_tree(source: Path, destination: Path) -> None:
    """复制 Memory 普通文件，并用 Backup API 单独复制其中的 SQLite。"""

    def ignore(_directory: str, names: list[str]) -> set[str]:
        """跳过锁、SQLite 主文件及其 WAL/SHM 辅助文件。"""

        return {
            name
            for name in names
            if name in _LOCK_FILE_NAMES
            or name.endswith(".lock")
            or name.endswith(".db")
            or name.endswith(".db-wal")
            or name.endswith(".db-shm")
        }

    shutil.copytree(source, destination, ignore=ignore)
    for database in source.rglob("*.db"):
        if database.is_file():
            _copy_sqlite(database, destination / database.relative_to(source))


def _same_file(left: Path, right: Path) -> bool:
    """按大小和字节内容判断两个普通文件是否相同。"""

    return left.stat().st_size == right.stat().st_size and left.read_bytes() == right.read_bytes()


def _append_jsonl_unique(source: Path, destination: Path) -> None:
    """按完整行去重追加 JSONL，保留既有顺序。"""

    existing = destination.read_text(encoding="utf-8").splitlines()
    seen = set(existing)
    additions = [
        line
        for line in source.read_text(encoding="utf-8").splitlines()
        if line and line not in seen
    ]
    if not additions:
        return
    body = "\n".join(existing + additions)
    destination.write_text(body + "\n", encoding="utf-8")


def _overlay_current_memory(
    source: Path,
    destination: Path,
    *,
    keep_legacy_paths: set[Path],
) -> None:
    """把当前候选新增的非数据库 Memory 文件合并进长期基线。"""

    if not source.is_dir():
        return
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        name = path.name
        if (
            name in _LOCK_FILE_NAMES
            or name.endswith(".lock")
            or name.endswith(".db")
            or name.endswith(".db-wal")
            or name.endswith(".db-shm")
        ):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if relative in keep_legacy_paths:
            continue
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            continue
        if _same_file(path, target):
            continue
        if relative.as_posix() in {
            "core.md",
            "dictionary-L0.md",
            "profile.md",
            "meta/dictionary-state.json",
        }:
            continue
        if target.suffix == ".jsonl" and target.name in {
            "access-log.jsonl",
            "outcome-log.jsonl",
        }:
            _append_jsonl_unique(path, target)
            continue
        raise DesktopDataMigrationError(
            f"conflicting Memory artifact requires an owner-specific merge: {relative}"
        )


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    """按 schema 顺序返回一个 SQLite 表的列名。"""

    return tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))


def _rewrite_root(value: Any, roots: tuple[Path, ...], target: Path) -> Any:
    """只替换以已知旧 Memory 根开头的路径字符串。"""

    if not isinstance(value, str):
        return value
    for root in roots:
        rendered = str(root)
        if value == rendered:
            return str(target)
        prefix = rendered + os.sep
        if value.startswith(prefix):
            return str(target) + os.sep + value[len(prefix) :]
    return value


def _merge_sessions_db(
    target_memory: Path,
    current_memory: Path,
    *,
    old_roots: tuple[Path, ...],
    published_memory: Path,
) -> None:
    """合并当前候选 registry，并把 normalized journal 路径改到新根。"""

    target_database = target_memory / "meta" / "sessions.db"
    current_database = current_memory / "meta" / "sessions.db"
    target = sqlite3.connect(target_database)
    target.row_factory = sqlite3.Row
    try:
        initialize_schema(target)
        for root in old_roots:
            target.execute(
                "UPDATE codex_turns SET journal_path = ? || substr(journal_path, ?) "
                "WHERE journal_path = ? OR journal_path LIKE ?",
                (
                    str(published_memory),
                    len(str(root)) + 1,
                    str(root),
                    str(root) + os.sep + "%",
                ),
            )
        if current_database.exists():
            source = sqlite3.connect(
                f"{current_database.resolve().as_uri()}?mode=ro", uri=True
            )
            source.row_factory = sqlite3.Row
            try:
                for table, key_columns in _SESSION_TABLE_KEYS.items():
                    source_columns = _table_columns(source, table)
                    target_columns = _table_columns(target, table)
                    columns = tuple(
                        column for column in source_columns if column in target_columns
                    )
                    placeholders = ", ".join("?" for _ in columns)
                    column_sql = ", ".join(columns)
                    key_where = " AND ".join(f"{key} = ?" for key in key_columns)
                    for row in source.execute(f"SELECT {column_sql} FROM {table}"):
                        values = [row[column] for column in columns]
                        if "journal_path" in columns:
                            index = columns.index("journal_path")
                            values[index] = _rewrite_root(
                                values[index], old_roots, published_memory
                            )
                        keys = [row[key] for key in key_columns]
                        existing = target.execute(
                            f"SELECT {column_sql} FROM {table} WHERE {key_where}",
                            keys,
                        ).fetchone()
                        if existing is not None:
                            if tuple(existing[column] for column in columns) == tuple(values):
                                continue
                            if table == "codex_turns" and _turn_lifecycle_rank(
                                dict(existing)
                            ) >= _turn_lifecycle_rank(dict(row)):
                                continue
                            if tuple(existing[column] for column in columns) != tuple(values):
                                raise DesktopDataMigrationError(
                                    f"conflicting sessions row: {table} {tuple(keys)}"
                                )
                        target.execute(
                            f"INSERT INTO {table}({column_sql}) VALUES ({placeholders})",
                            values,
                        )
            finally:
                source.close()
        target.commit()
    finally:
        target.close()


def _rewrite_json_value(value: Any, roots: tuple[Path, ...], target: Path) -> Any:
    """递归改写结构化 JSON 中的旧 Memory 根路径。"""

    if isinstance(value, dict):
        return {
            key: _rewrite_json_value(item, roots, target)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_json_value(item, roots, target) for item in value]
    return _rewrite_root(value, roots, target)


def _rewrite_structured_memory_paths(
    memory_root: Path,
    *,
    old_roots: tuple[Path, ...],
    published_memory: Path,
) -> None:
    """改写 Episode frontmatter 和 JSON manifest 中的旧绝对根路径。"""

    episodes = memory_root / "episodes"
    if episodes.is_dir():
        for path in episodes.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            frontmatter, body = _split_frontmatter(text)
            if frontmatter is None:
                continue
            rewritten = _rewrite_json_value(
                frontmatter, old_roots, published_memory
            )
            if rewritten != frontmatter:
                path.write_text(
                    _dump_frontmatter(rewritten, body), encoding="utf-8"
                )
    meta = memory_root / "meta"
    if meta.is_dir():
        for path in meta.rglob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rewritten = _rewrite_json_value(payload, old_roots, published_memory)
            if rewritten != payload:
                path.write_text(
                    json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )


def _load_titles(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """宽松读取按 runtime 分组的会话标题索引。"""

    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    titles = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(titles, dict):
        return {}
    return {
        str(runtime): {
            str(native_id): record
            for native_id, record in records.items()
            if isinstance(record, dict)
        }
        for runtime, records in titles.items()
        if isinstance(records, dict)
    }


def _prefer_title(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """按来源质量优先、更新时间次优选择同一原生会话标题。"""

    left_rank = _TITLE_SOURCE_RANK.get(str(left.get("source")), -1)
    right_rank = _TITLE_SOURCE_RANK.get(str(right.get("source")), -1)
    if left_rank != right_rank:
        return left if left_rank > right_rank else right
    return (
        left
        if str(left.get("updated_at", "")) >= str(right.get("updated_at", ""))
        else right
    )


def _merge_titles(legacy: Path, current: Path, destination: Path) -> None:
    """合并旧长期标题和当前候选新增标题。"""

    merged = _load_titles(legacy)
    for runtime, records in _load_titles(current).items():
        runtime_records = merged.setdefault(runtime, {})
        for native_id, record in records.items():
            existing = runtime_records.get(native_id)
            runtime_records[native_id] = (
                record if existing is None else _prefer_title(existing, record)
            )
    destination.write_text(
        json.dumps({"version": 1, "titles": merged}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def _merge_non_user_identities(
    legacy: Path,
    current: Path,
    destination: Path,
) -> None:
    """合并新旧非用户会话身份，并兼容历史 ``delegates`` 文件格式。

    Args:
        legacy: 旧开发数据根中的身份索引。
        current: 当前候选 App 数据根中的身份索引。
        destination: staging 中待发布的合并索引。

    Raises:
        DesktopDataMigrationError: 任一已有来源无法按冻结格式解析。
    """

    merged = {"claude_code": set(), "codex": set()}
    found = False
    for source in (legacy, current):
        if not source.exists():
            continue
        found = True
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DesktopDataMigrationError(
                f"non-user identity index is unreadable: {source}"
            ) from exc
        raw_ids = payload.get("native_session_ids") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(raw_ids, dict)
        ):
            raise DesktopDataMigrationError(
                f"non-user identity index has invalid schema: {source}"
            )
        for runtime in merged:
            values = raw_ids.get(runtime)
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value for value in values
            ):
                raise DesktopDataMigrationError(
                    f"non-user identity index has invalid {runtime} ids: {source}"
                )
            merged[runtime].update(values)
    if not found:
        return
    destination.write_text(
        json.dumps(
            {
                "version": 1,
                "native_session_ids": {
                    runtime: sorted(values) for runtime, values in merged.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _merge_workspaces(legacy: Path, current: Path, destination: Path) -> None:
    """按路径合并最近工作区，并保留较新的打开时间。"""

    source = current if current.exists() else legacy
    if source.exists():
        _copy_sqlite(source, destination)
    connection = sqlite3.connect(destination)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS recent_workspaces("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "path TEXT NOT NULL UNIQUE, opened_at TEXT NOT NULL)"
        )
        for database in (legacy, current):
            if not database.exists():
                continue
            reader = sqlite3.connect(
                f"{database.resolve().as_uri()}?mode=ro", uri=True
            )
            try:
                rows = reader.execute(
                    "SELECT path, opened_at FROM recent_workspaces"
                ).fetchall()
            finally:
                reader.close()
            for workspace, opened_at in rows:
                connection.execute(
                    "INSERT INTO recent_workspaces(path, opened_at) VALUES (?, ?) "
                    "ON CONFLICT(path) DO UPDATE SET opened_at = "
                    "CASE WHEN excluded.opened_at > opened_at "
                    "THEN excluded.opened_at ELSE opened_at END",
                    (workspace, opened_at),
                )
        connection.commit()
    finally:
        connection.close()


def _copy_current_app_state(plan: DesktopDataMigrationPlan, stage: Path) -> None:
    """复制当前候选拥有的数据库、binding 和显式配置。"""

    previous = plan.previous_app_root
    _copy_sqlite(previous / "trowel.db", stage / "trowel.db")
    for name in ("agent_sessions.json",):
        source = previous / name
        if source.exists():
            shutil.copy2(source, stage / name)
    _merge_non_user_identities(
        plan.legacy_root / "agent_sessions.delegates.json",
        previous / "agent_sessions.delegates.json",
        stage / "agent_sessions.delegates.json",
    )
    _merge_titles(
        plan.legacy_root / "agent_session_titles.json",
        previous / "agent_session_titles.json",
        stage / "agent_session_titles.json",
    )
    _merge_workspaces(
        plan.legacy_root / "workspaces.db",
        previous / "workspaces.db",
        stage / "workspaces.db",
    )
    if plan.config_source is not None:
        shutil.copy2(plan.config_source, stage / "config.toml")
        os.chmod(stage / "config.toml", 0o600)


def _write_manifest(plan: DesktopDataMigrationPlan, stage: Path) -> Path:
    """写入不含凭据正文的完成 manifest 和数据格式版本。"""

    manifest = {
        "version": _MIGRATION_VERSION,
        "status": "completed",
        "legacy_root": str(plan.legacy_root),
        "previous_app_root": str(plan.previous_app_root),
        "legacy_notes": plan.legacy_notes,
        "legacy_codex_turns": plan.legacy_codex_turns,
        "current_codex_turns": plan.current_codex_turns,
        "config_imported": plan.config_source is not None,
        "old_garden_imported": False,
    }
    path = stage / "migration-manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (stage / "data-format.json").write_text(
        json.dumps({"version": 1}, indent=2) + "\n", encoding="utf-8"
    )
    return path


def apply_desktop_data_migration(
    plan: DesktopDataMigrationPlan,
) -> DesktopDataMigrationResult:
    """在 staging 中构造、校验并原子发布一次迁移。

    Args:
        plan: 最近一次只读盘点得到的迁移计划。

    Returns:
        已发布目标与完成 manifest 路径。

    Raises:
        DesktopDataMigrationError: 计划有 blocker、运行状态已变化或合并冲突。
    """

    with _migration_lock(plan.target_root.parent):
        refreshed = plan_desktop_data_migration(
            target_root=plan.target_root,
            legacy_root=plan.legacy_root,
            previous_app_root=plan.previous_app_root,
            config_source=plan.config_source,
        )
        if not refreshed.ready:
            raise DesktopDataMigrationError("; ".join(refreshed.blockers))
        plan = refreshed
        shutdown_instance_id = _clean_previous_app_instance_id(
            plan.previous_app_root
        )
        if shutdown_instance_id is None:
            raise DesktopDataMigrationError(
                "previous App clean-exit evidence changed before migration started"
            )
        stage = plan.target_root.with_name(
            f".{plan.target_root.name}.migrating-{uuid.uuid4().hex}"
        )
        memory_target = stage / "memory"
        published_memory = plan.target_root / "memory"
        legacy_memory = plan.legacy_root / "memory"
        current_memory = plan.previous_app_root / "memory"
        try:
            _copy_memory_tree(legacy_memory, memory_target)
            _, overlap_blockers, keep_legacy_paths = _turn_overlap_audit(
                legacy_memory, current_memory
            )
            if overlap_blockers:
                raise DesktopDataMigrationError("; ".join(overlap_blockers))
            _overlay_current_memory(
                current_memory,
                memory_target,
                keep_legacy_paths=keep_legacy_paths,
            )
            _merge_sessions_db(
                memory_target,
                current_memory,
                old_roots=(legacy_memory, current_memory),
                published_memory=published_memory,
            )
            _rewrite_structured_memory_paths(
                memory_target,
                old_roots=(legacy_memory, current_memory),
                published_memory=published_memory,
            )
            _copy_current_app_state(plan, stage)
            manifest = _write_manifest(plan, stage)
            expected_turns = (
                plan.legacy_codex_turns
                + plan.current_codex_turns
                - plan.overlapping_codex_turns
            )
            actual_turns = _count_turns(memory_target)
            if actual_turns != expected_turns:
                raise DesktopDataMigrationError(
                    f"Codex turn count mismatch: expected {expected_turns}, got {actual_turns}"
                )
            final_shutdown_blockers = _previous_app_shutdown_blockers(
                plan.previous_app_root
            )
            if (
                final_shutdown_blockers
                or _clean_previous_app_instance_id(plan.previous_app_root)
                != shutdown_instance_id
            ):
                raise DesktopDataMigrationError(
                    "previous App lifecycle changed during migration; fully quit "
                    "Trowel and retry"
                )
            os.replace(stage, plan.target_root)
            return DesktopDataMigrationResult(
                status="completed",
                target_root=plan.target_root,
                manifest_path=plan.target_root / manifest.name,
            )
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def _default_paths() -> tuple[Path, Path, Path]:
    """返回当前 macOS 用户的目标、旧开发和上一候选根目录。"""

    home = Path.home()
    previous = home / "Library" / "Application Support" / "Trowel"
    return previous / "data", home / ".trowel", previous


def main(argv: list[str] | None = None) -> int:
    """打印真实 dry-run，或在显式 ``--apply`` 时执行离线迁移。"""

    default_target, default_legacy, default_previous = _default_paths()
    parser = argparse.ArgumentParser(description="migrate legacy Trowel desktop data")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--target", type=Path, default=default_target)
    parser.add_argument("--legacy-root", type=Path, default=default_legacy)
    parser.add_argument("--previous-app-root", type=Path, default=default_previous)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    plan = plan_desktop_data_migration(
        target_root=args.target,
        legacy_root=args.legacy_root,
        previous_app_root=args.previous_app_root,
        config_source=args.config,
    )
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    if not args.apply:
        return 0 if plan.ready else 2
    try:
        result = apply_desktop_data_migration(plan)
    except DesktopDataMigrationError as exc:
        print(f"migration refused: {exc}", file=sys.stderr)
        return 2
    print(f"migration completed: {result.target_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
