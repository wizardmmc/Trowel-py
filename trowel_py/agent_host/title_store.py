"""按原生会话身份持久保存用户可见标题。"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trowel_py.agent_host.binding import Runtime, TitleSource

_SCHEMA_VERSION = 1
_PERSISTED_SOURCES: frozenset[str] = frozenset(
    {"native", "prompt", "generated", "manual"}
)


@dataclass(frozen=True)
class SessionTitleRecord:
    """记录一个原生会话当前采用的标题及其来源。

    Attributes:
        runtime: 原生会话属于 Claude Code 还是 Codex。
        native_session_id: Claude Code session ID 或 Codex thread ID。
        title: 多开栏和历史列表显示的非空标题。
        source: 标题来自原生历史、首条提示词、后台生成还是手动改名。
        updated_at: 最后一次写入标题时的本地 ISO 时间。
    """

    runtime: Runtime
    native_session_id: str
    title: str
    source: TitleSource
    updated_at: str


def resolve_title_store_path(binding_path: Path) -> Path:
    """返回与 binding 文件相邻的原生会话标题索引路径。

    Args:
        binding_path: Agent Host 会话 binding 文件路径。

    Returns:
        使用相同目录并把文件名从 ``agent_sessions`` 改为
        ``agent_session_titles`` 的 JSON 路径；其他名称追加 ``_titles``。
    """

    stem = binding_path.stem
    title_stem = (
        "agent_session_titles" if stem == "agent_sessions" else f"{stem}_titles"
    )
    return binding_path.with_name(f"{title_stem}{binding_path.suffix or '.json'}")


class SessionTitleStore:
    """读写按 runtime 与原生会话 ID 索引的持久标题。"""

    def __init__(self, path: Path) -> None:
        """创建使用指定 JSON 文件的标题存储。

        Args:
            path: 保存标题索引的本地 JSON 文件路径。
        """

        self._path = path
        self._thread_lock = threading.RLock()

    @property
    def path(self) -> Path:
        """返回标题索引文件路径。"""

        return self._path

    def _load_raw(self) -> dict[str, dict[str, dict[str, Any]]]:
        """读取并宽松过滤标题索引的 runtime 分组。"""

        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(payload, dict):
            return {}
        titles = payload.get("titles")
        if not isinstance(titles, dict):
            return {}
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for runtime, records in titles.items():
            if not isinstance(runtime, str) or not isinstance(records, dict):
                continue
            out[runtime] = {
                native_id: record
                for native_id, record in records.items()
                if isinstance(native_id, str) and isinstance(record, dict)
            }
        return out

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        """锁住一次读取或完整读改写周期，避免 dev/stable 并发丢标题。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_name(self._path.name + ".lock")
        with self._thread_lock, lock_path.open("a+b") as handle:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), operation)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _save_raw(self, titles: dict[str, dict[str, dict[str, Any]]]) -> None:
        """用同目录临时文件原子替换完整标题索引。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{self._path.name}.",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": _SCHEMA_VERSION, "titles": titles},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def get(
        self, runtime: Runtime, native_session_id: str
    ) -> SessionTitleRecord | None:
        """读取指定原生会话当前保存的标题。

        损坏、空标题和未知来源记录会被当作不存在，避免阻断历史列表。
        """

        with self._lock(exclusive=False):
            raw = self._load_raw().get(runtime.value, {}).get(native_session_id)
        if not isinstance(raw, dict):
            return None
        title = raw.get("title")
        source = raw.get("source")
        updated_at = raw.get("updated_at")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(source, str)
            or source not in _PERSISTED_SOURCES
            or not isinstance(updated_at, str)
        ):
            return None
        return SessionTitleRecord(
            runtime=runtime,
            native_session_id=native_session_id,
            title=title,
            source=source,  # type: ignore[arg-type]
            updated_at=updated_at,
        )

    def put(self, record: SessionTitleRecord) -> None:
        """新增或覆盖一个原生会话标题，同时保留其他记录。

        Args:
            record: 要持久保存的完整标题记录。
        """

        if not record.native_session_id or not record.title.strip():
            raise ValueError("native session id and title must be non-empty")
        if record.source not in _PERSISTED_SOURCES:
            raise ValueError(f"title source {record.source!r} cannot be persisted")
        with self._lock(exclusive=True):
            titles = self._load_raw()
            runtime_titles = titles.setdefault(record.runtime.value, {})
            runtime_titles[record.native_session_id] = {
                "title": record.title,
                "source": record.source,
                "updated_at": record.updated_at,
            }
            self._save_raw(titles)
