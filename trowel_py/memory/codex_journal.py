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
_TERMINAL_TYPES = frozenset({CodexEventType.FINISHED, CodexEventType.INTERRUPTED})


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
        memory_eligibility: str = "eligible",
        now_fn: NowFn | None = None,
    ) -> None:
        self._root = memory_root
        self._trowel_session_id = trowel_session_id
        self._workdir = workdir
        self._memory_enabled = memory_enabled
        self._profile_enabled = profile_enabled
        self._session_kind = session_kind
        self._memory_eligibility = memory_eligibility
        self._now = now_fn or datetime.now
        self._registered: set[tuple[str, str]] = set()
        self._excluded: set[tuple[str, str]] = set()
        self._failed: set[tuple[str, str]] = set()
        self._handles: dict[tuple[str, str], IO[str]] = {}
        # Codex session 创建发生在 native turn 前；这里失败可安全拒绝创建。
        (self._root / "meta" / "codex-turns").mkdir(parents=True, exist_ok=True)
        conn = open_sessions_db(self._root)
        try:
            create_sessions_repository(conn)
        finally:
            conn.close()

    def record(self, event: CodexEvent, binding: Any | None) -> None:
        """同步追加单条事件；transport 回调返回前保证 terminal 已封口。"""

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
            create_sessions_repository(conn).complete_codex_turn(
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
        conn = open_sessions_db(self._root)
        try:
            create_sessions_repository(conn).register_codex_turn(
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
                memory_eligibility=self._memory_eligibility,
            )
        finally:
            conn.close()

    def _turn_path(self, thread_id: str, turn_id: str) -> Path:
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
        handle = self._handles.pop(key, None)
        if handle is not None:
            with suppress(OSError):
                handle.close()


def _terminal_status(event: CodexEvent) -> str | None:
    if event.type in _TERMINAL_TYPES:
        return str(event.payload.get("status") or event.type.value)
    if (
        event.type is CodexEventType.ERROR
        and event.payload.get("kind") != "native_error"
    ):
        return str(event.payload.get("status") or "failed")
    return None


def _completed_at(payload: Mapping[str, Any], fallback: datetime) -> str:
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
    """修复 terminal 已 fsync、但 completed transaction 未提交的崩溃窗口。"""

    conn = open_sessions_db(memory_root)
    recovered = 0
    try:
        repo = create_sessions_repository(conn)
        for turn in repo.find_unsealed_codex_turns():
            terminal = _read_terminal(
                Path(turn.journal_path), turn.thread_id, turn.turn_id
            )
            if terminal is None:
                continue
            status, completed_at = terminal
            repo.complete_codex_turn(
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
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed
