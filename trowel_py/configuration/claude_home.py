"""管理 Claude 兼容连接独占的配置家、继承发布与删除墓碑。"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from trowel_py.application_paths import resolve_application_data_root

_INHERITED_MARKER = ".trowel-inherited.json"
_DELETED_MARKER = ".trowel-deleted.json"
_SUPPORTED_DIRECTORIES = (
    "skills",
    "commands",
    "agents",
    "rules",
    "output-styles",
)
_SUPPORTED_FILES = ("CLAUDE.md", "settings.json")
_MANAGED_NAMES = (*_SUPPORTED_DIRECTORIES, *_SUPPORTED_FILES, _INHERITED_MARKER)
_ROOT_LOCKS: dict[Path, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


class ClaudeConnectionHomeError(RuntimeError):
    """表示连接家路径、来源配置或原子发布无法安全完成。"""


@dataclass(frozen=True)
class ClaudeHomeTombstone:
    """记录连接家原地转为保留墓碑后的补偿路径。

    Attributes:
        home: 始终保持不变的连接家路径，保证冻结档案仍能定位历史。
        tombstone: 软删除提交后保留在家内的私有标记文件。
    """

    home: Path
    tombstone: Path


@dataclass(frozen=True)
class ClaudeInheritanceResult:
    """返回覆盖式继承后可以公开的脱敏事实。

    Attributes:
        inherited: 本次继承是否完成并写入成功标记。
        copied_entries: 本次从真实全局家复制的顶层配置名称。
        settings_keys: 删除 env 后保留的 settings 顶层键名。
    """

    inherited: bool
    copied_entries: tuple[str, ...]
    settings_keys: tuple[str, ...]


class ClaudeConnectionHomeStore:
    """在应用私有根中分配和更新每项 Claude 连接的独立家。"""

    def __init__(
        self,
        root: Path | None = None,
        *,
        global_home: Path | None = None,
    ) -> None:
        """保存托管根与恒定的真实全局继承来源。

        Args:
            root: Trowel 托管 Claude 连接家的父目录；None 使用当前应用数据根。
            global_home: 用户真实的全局 Claude 家；None 使用 `~/.claude`。
        """

        self.root = (
            root
            if root is not None
            else resolve_application_data_root() / "claude-connections"
        ).expanduser().absolute()
        self.global_home = (
            global_home if global_home is not None else Path.home() / ".claude"
        ).expanduser().absolute()
        with _ROOT_LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(self.root, threading.RLock())

    @property
    def shared_plugin_root(self) -> Path:
        """返回所有连接共享物理安装的真实全局 plugin 根。"""

        return self.global_home / "plugins"

    def home_for(self, connection_id: str) -> Path:
        """按连接 UUID 返回不会跟随槽位符号链接的托管家路径。

        Args:
            connection_id: 设置域分配的稳定连接 UUID。

        Returns:
            托管根下以规范 UUID 命名的连接家路径。

        Raises:
            ClaudeConnectionHomeError: 连接 ID 无效或槽位是符号链接。
        """

        try:
            normalized = str(uuid.UUID(connection_id))
        except ValueError as exc:
            raise ClaudeConnectionHomeError("Claude 连接家标识无效") from exc
        home = self.root / normalized
        if home.is_symlink():
            raise ClaudeConnectionHomeError("Claude 连接家不能是符号链接")
        return home

    def ensure_home(self, connection_id: str) -> Path:
        """惰性创建仅当前账号可访问的连接家并返回路径。"""

        with self._lock:
            self._ensure_root()
            home = self.home_for(connection_id)
            if home.exists():
                if not home.is_dir() or home.is_symlink():
                    raise ClaudeConnectionHomeError("Claude 连接家路径不安全")
            else:
                home.mkdir(mode=0o700)
            home.chmod(0o700)
            # 应用在写入软删除标记后异常退出时，活动连接的
            # 下次解析就是补偿点。已提交删除的连接不会再调用此方法。
            (home / _DELETED_MARKER).unlink(missing_ok=True)
            return home

    def is_inherited(self, connection_id: str) -> bool:
        """判断连接家是否具有一次完整继承成功后的标记。"""

        marker = self.home_for(connection_id) / _INHERITED_MARKER
        return marker.is_file() and not marker.is_symlink()

    def inherit_global(
        self,
        connection_id: str,
        *,
        forbidden_values: Iterable[str] = (),
    ) -> ClaudeInheritanceResult:
        """把真实全局家的受支持配置覆盖发布到指定连接家。

        整个 `env` 块会从 settings 副本删除。发布只替换受支持的配置入口，保留
        `projects`、`.claude.json` 等连接原生状态；任一步失败都会恢复发布前内容。

        Args:
            connection_id: 接收全局配置副本的 Claude 兼容连接 ID。
            forbidden_values: Trowel 已知的凭据原值；发布前对副本
                执行字节级 canary 扫描，命中任一值就整体拒绝。

        Returns:
            不含配置正文的继承结果。

        Raises:
            ClaudeConnectionHomeError: 全局配置损坏、路径不安全或发布失败。
        """

        with self._lock:
            home = self.ensure_home(connection_id)
            stage = self.root / f".inherit-stage-{connection_id}-{uuid.uuid4().hex}"
            backup = self.root / f".inherit-backup-{connection_id}-{uuid.uuid4().hex}"
            stage.mkdir(mode=0o700)
            copied_entries: list[str] = []
            settings_keys: tuple[str, ...] = ()
            try:
                for name in _SUPPORTED_DIRECTORIES:
                    source = self.global_home / name
                    if not source.exists() and not source.is_symlink():
                        continue
                    if not source.is_dir():
                        raise ClaudeConnectionHomeError(
                            f"全局 Claude 配置目录不可继承：{name}"
                        )
                    # 全局 skill 常用符号链接复用其他工具目录。继承时按当前
                    # 指向复制实体内容，staging 因而不再依赖之后可能变化的链接目标。
                    shutil.copytree(source, stage / name, symlinks=False)
                    copied_entries.append(name)
                claude_md = self.global_home / "CLAUDE.md"
                if claude_md.exists() or claude_md.is_symlink():
                    self._copy_regular_file(claude_md, stage / "CLAUDE.md")
                    copied_entries.append("CLAUDE.md")
                settings_source = self.global_home / "settings.json"
                if settings_source.exists() or settings_source.is_symlink():
                    settings = self._read_settings(settings_source)
                    settings.pop("env", None)
                    settings_keys = tuple(sorted(settings))
                    self._write_private_json(stage / "settings.json", settings)
                    copied_entries.append("settings.json")
                self._assert_forbidden_values_absent(stage, forbidden_values)
                self._write_private_json(
                    stage / _INHERITED_MARKER,
                    {
                        "inherited_at": datetime.now(UTC).isoformat(),
                        "source": "global_claude_home",
                    },
                )
                self._publish(home, stage, backup)
            except ClaudeConnectionHomeError:
                raise
            except (OSError, ValueError, TypeError) as exc:
                raise ClaudeConnectionHomeError(
                    "继承全局 Claude 配置失败"
                ) from exc
            finally:
                self._remove_exact(stage)
                self._remove_exact(backup)
            return ClaudeInheritanceResult(
                inherited=True,
                copied_entries=tuple(copied_entries),
                settings_keys=settings_keys,
            )

    def stage_deletion(self, connection_id: str) -> ClaudeHomeTombstone | None:
        """在现存连接家原路径写入不会自动清理的墓碑标记。"""

        with self._lock:
            home = self.home_for(connection_id)
            if not home.exists():
                return None
            if not home.is_dir() or home.is_symlink():
                raise ClaudeConnectionHomeError("Claude 连接家路径不安全")
            tombstone = home / _DELETED_MARKER
            if tombstone.is_symlink() or (tombstone.exists() and not tombstone.is_file()):
                raise ClaudeConnectionHomeError("Claude 连接家墓碑路径不安全")
            if not tombstone.exists():
                self._write_private_json(
                    tombstone,
                    {
                        "connection_id": connection_id,
                        "deleted_at": datetime.now(UTC).isoformat(),
                    },
                )
            return ClaudeHomeTombstone(home=home, tombstone=tombstone)

    def restore_deletion(self, deletion: ClaudeHomeTombstone | None) -> None:
        """数据库软删除失败时删除墓碑标记，恢复活动语义。"""

        if deletion is None:
            return
        with self._lock:
            if deletion.tombstone.is_file() and not deletion.tombstone.is_symlink():
                deletion.tombstone.unlink()

    def projects_roots(self) -> tuple[Path, ...]:
        """返回活动家和保留墓碑中可供历史扫描的 projects 根。"""

        if not self.root.is_dir() or self.root.is_symlink():
            return ()
        roots: list[Path] = []
        with self._lock:
            for child in self.root.iterdir():
                if not child.is_dir() or child.is_symlink():
                    continue
                if self._connection_id_from_directory(child.name) is None:
                    continue
                roots.append(child / "projects")
        return tuple(sorted(roots, key=str))

    def _ensure_root(self) -> None:
        """创建并收紧托管根权限，同时拒绝根目录符号链接。"""

        if self.root.is_symlink():
            raise ClaudeConnectionHomeError("Claude 连接家根目录不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    @staticmethod
    def _connection_id_from_directory(name: str) -> str | None:
        """从活动家或保留墓碑名称中提取规范连接 UUID。"""

        candidate = name
        if name.startswith(".deleted-"):
            candidate = name.removeprefix(".deleted-")[:36]
        try:
            normalized = str(uuid.UUID(candidate))
        except ValueError:
            return None
        return normalized if candidate == normalized else None

    @staticmethod
    def _copy_regular_file(source: Path, target: Path) -> None:
        """按当前内容复制普通文件并移除 group/other 权限。"""

        if not source.is_file():
            raise ClaudeConnectionHomeError(
                f"全局 Claude 配置文件不可继承：{source.name}"
            )
        shutil.copy2(source, target)
        target.chmod(target.stat().st_mode & 0o700)

    @staticmethod
    def _read_settings(path: Path) -> dict[str, Any]:
        """读取严格 JSON 对象形式的全局 settings。"""

        if not path.is_file():
            raise ClaudeConnectionHomeError("全局 Claude settings.json 路径不安全")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ClaudeConnectionHomeError(
                "全局 Claude settings.json 无法解析"
            ) from exc
        if not isinstance(value, dict):
            raise ClaudeConnectionHomeError(
                "全局 Claude settings.json 必须是 JSON 对象"
            )
        return value

    @staticmethod
    def _assert_forbidden_values_absent(
        stage: Path,
        forbidden_values: Iterable[str],
    ) -> None:
        """扫描即将发布的普通文件，阻止已知凭据原值被复制。

        源配置中的符号链接会先按当前目标复制成实体内容；staging 若仍出现
        符号链接则拒绝发布，避免扫描后目标变化。

        Args:
            stage: 本次继承的 staging 根。
            forbidden_values: 不允许出现的已知凭据原值。

        Raises:
            ClaudeConnectionHomeError: 发现符号链接或已知凭据原值。
        """

        canaries = tuple(
            value.encode("utf-8")
            for value in forbidden_values
            if isinstance(value, str) and value
        )
        for path in stage.rglob("*"):
            if path.is_symlink():
                raise ClaudeConnectionHomeError(
                    "全局 Claude 配置包含无法安全继承的符号链接"
                )
            if not path.is_file() or not canaries:
                continue
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise ClaudeConnectionHomeError(
                    "全局 Claude 配置无法完成凭据扫描"
                ) from exc
            if any(canary in content for canary in canaries):
                raise ClaudeConnectionHomeError(
                    "全局 Claude 配置中仍包含 Trowel 已知凭据，已拒绝继承"
                )

    @staticmethod
    def _write_private_json(path: Path, value: dict[str, Any]) -> None:
        """以 `0600` 写入不会包含供应商 env 的私有 JSON 文件。"""

        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @classmethod
    def _publish(cls, home: Path, stage: Path, backup: Path) -> None:
        """逐入口发布 staging 内容，并在失败时恢复全部旧入口。"""

        backup.mkdir(mode=0o700)
        published: list[str] = []
        moved_old: list[str] = []
        try:
            for name in _MANAGED_NAMES:
                target = home / name
                staged = stage / name
                if target.exists() or target.is_symlink():
                    target.rename(backup / name)
                    moved_old.append(name)
                if staged.exists() or staged.is_symlink():
                    staged.rename(target)
                    published.append(name)
        except BaseException:
            for name in reversed(published):
                cls._remove_exact(home / name)
            for name in reversed(moved_old):
                saved = backup / name
                if saved.exists() or saved.is_symlink():
                    saved.rename(home / name)
            raise

    @staticmethod
    def _remove_exact(path: Path) -> None:
        """删除一个已解析的 staging/backup 入口而不跟随符号链接。"""

        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
