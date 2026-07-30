"""持久保存由 Trowel 委派创建的原生会话身份。"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trowel_py.agent_host.binding import Runtime

_SCHEMA_VERSION = 1
_IDS_FIELD = "native_session_ids"


class DelegateIdentityIndexError(RuntimeError):
    """表示委派身份索引损坏或版本不受支持。"""


def delegate_identity_path(bindings_path: Path) -> Path:
    """返回与会话 binding 文件隔离的委派身份索引路径。

    Args:
        bindings_path: Agent Host 会话 binding 文件路径。

    Returns:
        与 binding 文件位于同一目录、但不会随 binding 删除而改写的索引路径。
    """

    return bindings_path.with_name(f"{bindings_path.stem}.delegates.json")


class DelegateIdentityStore:
    """在本机 JSON 索引中永久记录委派会话的原生 ID。

    索引只保存运行工具和原生会话 ID，不保存标题、工作目录、任务文本或其他会话
    内容。没有删除接口；清理 Trowel binding 时，历史过滤身份仍会保留。

    Attributes:
        path: 委派身份索引文件路径。
    """

    def __init__(self, path: Path) -> None:
        """创建使用指定文件的委派身份索引。

        Args:
            path: 保存委派原生会话 ID 的 JSON 文件路径。
        """

        self._path = path

    @property
    def path(self) -> Path:
        """返回委派身份索引文件路径。"""

        return self._path

    def ids(self, runtime: Runtime) -> frozenset[str]:
        """返回指定运行工具中全部已知的委派原生会话 ID。

        Args:
            runtime: 要读取 Claude Code 还是 Codex 的委派身份。

        Returns:
            不可变的原生会话 ID 集合。
        """

        with self._lock(exclusive=False):
            return frozenset(self._load()[runtime])

    def add(self, runtime: Runtime, native_session_id: str) -> None:
        """幂等记录一个已经确认属于委派会话的原生 ID。

        Args:
            runtime: 该原生会话由 Claude Code 还是 Codex 保存。
            native_session_id: Claude Code session ID 或 Codex thread ID。

        Raises:
            ValueError: 原生会话 ID 为空。
            DelegateIdentityIndexError: 现有索引损坏或版本不受支持。
        """

        if not native_session_id:
            raise ValueError("delegate native session id cannot be empty")
        with self._lock(exclusive=True):
            identities = self._load()
            if native_session_id in identities[runtime]:
                return
            identities[runtime].add(native_session_id)
            self._save(identities)

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        """锁住索引读取或完整读改写周期。

        Args:
            exclusive: 写入时使用独占锁；只读时使用共享锁，避免读取正在提交的旧状态。
        """

        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_name(self._path.name + ".lock")
        with lock_path.open("a+b") as handle:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), operation)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self) -> dict[Runtime, set[str]]:
        """读取并严格校验当前索引；文件不存在时返回两个空集合。"""

        empty = {Runtime.CLAUDE_CODE: set(), Runtime.CODEX: set()}
        if not self._path.exists():
            return empty
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise DelegateIdentityIndexError(
                "delegate identity index is unreadable"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _SCHEMA_VERSION
            or not isinstance(payload.get(_IDS_FIELD), dict)
        ):
            raise DelegateIdentityIndexError(
                "delegate identity index has invalid schema"
            )
        raw_ids: dict[str, Any] = payload[_IDS_FIELD]
        identities: dict[Runtime, set[str]] = {}
        for runtime in Runtime:
            values = raw_ids.get(runtime.value)
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value for value in values
            ):
                raise DelegateIdentityIndexError(
                    f"delegate identity index has invalid {runtime.value} ids"
                )
            identities[runtime] = set(values)
        return identities

    def _save(self, identities: dict[Runtime, set[str]]) -> None:
        """通过同目录临时文件原子发布完整索引。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _SCHEMA_VERSION,
            _IDS_FIELD: {
                runtime.value: sorted(identities[runtime]) for runtime in Runtime
            },
        }
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temporary_name, self._path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
