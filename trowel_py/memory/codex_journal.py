"""持久化 Trowel 托管的 Codex normalized turn 事件。"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Callable, Mapping

from trowel_py.codex_host.events import CodexEvent, CodexEventType
from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)

NowFn = Callable[[], datetime]
_TERMINAL_TYPES = frozenset(
    {CodexEventType.FINISHED, CodexEventType.INTERRUPTED}
)


class CodexTurnJournal:
    """每个原生 turn 写一个 JSONL；terminal fsync 后才推进 SQLite 水位。"""

    def __init__(
        self,
        memory_root: Path,
        *,
        trowel_session_id: str,
        workdir: str,
        memory_enabled: bool,
        profile_enabled: bool,
        session_kind: str = "user",
        now_fn: NowFn | None = None,
    ) -> None:
        """准备轮次日志目录，并保存后续登记轮次所需的会话信息。

        Args:
            memory_root: memory 数据目录；轮次日志和会话数据库均位于该目录下。
            trowel_session_id: 这些 Codex 轮次所属的 Trowel 会话 ID。
            workdir: 会话使用的工作目录，登记轮次时一并保存。
            memory_enabled: 创建会话时冻结的 memory 开关，登记轮次时一并保存。
            profile_enabled: 创建会话时冻结的 profile 开关，登记轮次时一并保存。
            session_kind: 会话类别；daily review 只读取 ``"user"`` 会话的轮次。
            now_fn: 生成日志记录时间的时钟；未提供时使用本地当前时间。
        """
        self._root = memory_root
        self._trowel_session_id = trowel_session_id
        self._workdir = workdir
        self._memory_enabled = memory_enabled
        self._profile_enabled = profile_enabled
        self._session_kind = session_kind
        self._now = now_fn or datetime.now
        self._registered: set[tuple[str, str]] = set()
        self._excluded: set[tuple[str, str]] = set()
        self._failed: set[tuple[str, str]] = set()
        self._handles: dict[tuple[str, str], IO[str]] = {}
        # Trowel 会话在 Codex 原生轮次开始前创建，因此初始化失败可直接阻止会话启动。
        (self._root / "meta" / "codex-turns").mkdir(parents=True, exist_ok=True)
        conn = open_sessions_db(self._root)
        try:
            create_sessions_repository(conn)
        finally:
            conn.close()

    def record(self, event: CodexEvent, binding: Any | None) -> None:
        """记录一条 Codex 事件，并在终态事件落盘后封存对应轮次。

        缺少 thread ID 或 turn ID 的事件会被忽略。首次看到轮次时会固化当前模型
        绑定；``TURN_STARTED`` 明确标记 ``memory_eligible=False`` 时，整个轮次
        不再写入。写日志失败时会关闭句柄并重新抛出异常，随后静默忽略该轮次的
        其他事件。

        Args:
            event: 已规范化的 Codex 事件。
            binding: 当前模型绑定；首次登记轮次时读取模型、推理强度和供应商。

        Raises:
            OSError: 写入轮次日志失败。
        """

        if not event.thread_id or not event.turn_id:
            return
        now = self._now()
        path = self._turn_path(event.thread_id, event.turn_id)
        key = (event.thread_id, event.turn_id)
        if key in self._excluded:
            return
        if (
            event.type is CodexEventType.TURN_STARTED
            and event.payload.get("memory_eligible") is False
        ):
            self._excluded.add(key)
            return
        if key in self._failed:
            return
        model = str(getattr(binding, "model", "") or "")
        effort = str(getattr(binding, "reasoning_effort", "") or "")
        provider = str(getattr(binding, "model_provider", "") or "")
        if key not in self._registered:
            self._register_turn(
                event,
                path,
                now,
                model=model,
                effort=effort,
                provider=provider,
            )
            self._registered.add(key)
        terminal = _terminal_status(event)
        try:
            self._append(key, path, event, now, sync=terminal is not None)
        except OSError:
            self._failed.add(key)
            self._close_handle(key)
            raise
        if terminal is None:
            return
        self._close_handle(key)
        if key in self._failed:
            return
        conn = open_sessions_db(self._root)
        try:
            create_sessions_repository(conn).codex.complete_turn(
                event.thread_id,
                event.turn_id,
                status=terminal,
                completed_at=_completed_at(event.payload, now),
            )
        finally:
            conn.close()

    def _register_turn(
        self,
        event: CodexEvent,
        path: Path,
        now: datetime,
        *,
        model: str,
        effort: str,
        provider: str,
    ) -> None:
        """登记首次看到的原生轮次，并固化其会话归属和模型绑定。"""
        conn = open_sessions_db(self._root)
        try:
            create_sessions_repository(conn).codex.register_turn(
                thread_id=event.thread_id or "",
                turn_id=event.turn_id or "",
                trowel_session_id=self._trowel_session_id,
                workdir=self._workdir,
                journal_path=str(path),
                registered_at=now.isoformat(timespec="microseconds"),
                model=model,
                effort=effort,
                provider=provider,
                memory_enabled=self._memory_enabled,
                profile_enabled=self._profile_enabled,
                session_kind=self._session_kind,
            )
        finally:
            conn.close()

    def _turn_path(self, thread_id: str, turn_id: str) -> Path:
        """用 thread ID 和 turn ID 各自 SHA-256 摘要的前 20 个十六进制字符组成路径。"""
        thread_key = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:20]
        turn_key = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:20]
        return self._root / "meta" / "codex-turns" / thread_key / f"{turn_key}.jsonl"

    def _append(
        self,
        key: tuple[str, str],
        path: Path,
        event: CodexEvent,
        now: datetime,
        *,
        sync: bool,
    ) -> None:
        """追加一条规范化事件；``sync`` 为真时在返回前同步到磁盘。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = event.as_dict()
        payload["timestamp"] = now.astimezone().isoformat(timespec="microseconds")
        handle = self._handles.get(key)
        if handle is None:
            handle = path.open("a", encoding="utf-8")
            self._handles[key] = handle
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        if sync:
            handle.flush()
            os.fsync(handle.fileno())

    def _close_handle(self, key: tuple[str, str]) -> None:
        """关闭并移除指定轮次的日志句柄，忽略关闭阶段的 I/O 错误。"""
        handle = self._handles.pop(key, None)
        if handle is not None:
            with suppress(OSError):
                handle.close()


def _terminal_status(event: CodexEvent) -> str | None:
    """返回终态事件的状态，缺失时按事件类型生成默认值。

    ``FINISHED`` 和 ``INTERRUPTED`` 默认使用各自的事件类型，非
    ``native_error`` 的 ``ERROR`` 默认使用 ``"failed"``。其他事件返回 None。
    """
    if event.type in _TERMINAL_TYPES:
        return str(event.payload.get("status") or event.type.value)
    if event.type is CodexEventType.ERROR and event.payload.get("kind") != "native_error":
        return str(event.payload.get("status") or "failed")
    return None


def _completed_at(payload: Mapping[str, Any], fallback: datetime) -> str:
    """把 payload 中的完成时间转换为本地无时区的 ISO 字符串。

    数字按 Unix 时间戳解析，字符串按 ISO 格式解析；缺失或无法解析时使用记录时间。
    """
    raw = payload.get("completed_at")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw).isoformat(timespec="microseconds")
    if isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed.isoformat(timespec="microseconds")
    return fallback.isoformat(timespec="microseconds")


def recover_sealed_codex_turns(memory_root: Path) -> int:
    """根据已落盘的终态事件封存数据库中尚未完成的 Codex 轮次。

    日志末条非空记录必须是 JSON 对象，其原生 ID 必须匹配数据库轮次，payload
    必须是对象，事件类型与状态必须属于支持的组合，且 timestamp 必须可解析。

    Args:
        memory_root: 包含会话数据库和轮次日志的 memory 数据目录。

    Returns:
        本次成功封存的轮次数量。
    """

    conn = open_sessions_db(memory_root)
    recovered = 0
    try:
        repo = create_sessions_repository(conn)
        for turn in repo.codex.list_unsealed_turns():
            terminal = _read_terminal(Path(turn.journal_path), turn.thread_id, turn.turn_id)
            if terminal is None:
                continue
            status, completed_at = terminal
            repo.codex.complete_turn(
                turn.thread_id,
                turn.turn_id,
                status=status,
                completed_at=completed_at,
            )
            recovered += 1
    finally:
        conn.close()
    return recovered


def _read_terminal(
    path: Path,
    thread_id: str,
    turn_id: str,
) -> tuple[str, str] | None:
    """校验日志末条非空记录，并返回可用于封存轮次的状态和完成时间。

    仅接受 ``finished/completed``、``interrupted/interrupted`` 和
    ``error/failed`` 三组事件类型与状态。
    """
    last_line = ""
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
    except (OSError, UnicodeDecodeError):
        return None
    try:
        event = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    if event.get("thread_id") != thread_id or event.get("turn_id") != turn_id:
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    type_ = event.get("type")
    status = payload.get("status")
    if (type_, status) not in {
        ("finished", "completed"),
        ("interrupted", "interrupted"),
        ("error", "failed"),
    }:
        return None
    fallback = _parse_recorded_at(event.get("timestamp"))
    if fallback is None:
        return None
    return str(status), _completed_at(payload, fallback)


def _parse_recorded_at(value: object) -> datetime | None:
    """把 ISO 格式的日志记录时间解析为本地无时区时间。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed
