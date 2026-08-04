"""从 sessions registry、binding store 和原生日志只读生成 Agent 观察结果。"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from trowel_py.agent_host.binding import SessionBinding
from trowel_py.agent_host.store import BindingStore
from trowel_py.memory.sessions_repo import open_sessions_db_readonly
from trowel_py.statistics.agent.claude import analyze_claude_binding_many
from trowel_py.statistics.agent.codec import parse_timestamp
from trowel_py.statistics.agent.codex import analyze_codex_session_many
from trowel_py.statistics.agent.models import (
    ClaudeBindingSource,
    CodexTurnSource,
    SessionObservation,
    SessionStatus,
)
from trowel_py.statistics.window import StatisticsWindow

_SESSION_STATUSES = frozenset(
    {"completed", "running", "interrupted", "failed", "unknown"}
)


class FileAgentObservationReader:
    """组合本机持久事实并按请求临时读取，不持有 SQLite 长连接。

    Attributes:
        memory_root: 包含 `meta/sessions.db` 的 Memory 根目录。
        binding_store: 提供仍连接或运行的 Trowel session 状态。
    """

    def __init__(self, memory_root: Path, binding_store: BindingStore) -> None:
        """保存只读来源位置；构造时不打开或创建文件。

        Args:
            memory_root: Trowel 当前 Memory 根目录。
            binding_store: Agent Host 使用的同一个持久 binding store。
        """

        self._memory_root = memory_root
        self._binding_store = binding_store

    def read(self, window: StatisticsWindow) -> list[SessionObservation]:
        """读取与查询窗相交的用户 session 事实。

        Args:
            window: 已解析的统计半开时间窗。

        Returns:
            双 runtime adapter 生成的统一 session 观察结果；数据库尚不存在时为空。
        """

        return self.read_many((window,))[0]

    def read_many(
        self,
        windows: Sequence[StatisticsWindow],
    ) -> tuple[list[SessionObservation], ...]:
        """读取一次 registry 和原生日志，再生成多个时间窗的 session 事实。

        Args:
            windows: 要从同一份来源快照生成的统计半开时间窗。

        Returns:
            与输入时间窗顺序一致的统一 session 观察结果。
        """

        requested = tuple(windows)
        if not requested:
            return ()
        connection = open_sessions_db_readonly(self._memory_root)
        if connection is None:
            return tuple([] for _ in requested)
        try:
            active = self._active_user_bindings()
            observations: list[list[SessionObservation]] = [
                [] for _ in requested
            ]
            for source in self._claude_sources(connection, active, requested):
                for index, observation in enumerate(
                    analyze_claude_binding_many(source, requested)
                ):
                    if observation is not None:
                        observations[index].append(observation)
            for sources in self._codex_sources(connection, active, requested):
                for index, observation in enumerate(
                    analyze_codex_session_many(sources, requested)
                ):
                    if observation is not None:
                        observations[index].append(observation)
            return tuple(observations)
        finally:
            connection.close()

    def _active_user_bindings(self) -> dict[str, SessionBinding]:
        """读取当前 binding store，并忽略损坏或并发消失的状态文件。"""

        try:
            bindings = self._binding_store.list_all()
        except (OSError, ValueError, KeyError):
            return {}
        return {
            binding.session_id: binding
            for binding in bindings
            if binding.session_kind == "user"
        }

    def _claude_sources(
        self,
        connection: sqlite3.Connection,
        active: dict[str, SessionBinding],
        windows: Sequence[StatisticsWindow],
    ) -> list[ClaudeBindingSource]:
        """读取可能与任一时间窗相交的 CC binding 水位。"""

        columns = _columns(connection, "session_bindings")
        status_sql = "b.status" if "status" in columns else "'unknown'"
        completed_sql = "b.completed_at" if "completed_at" in columns else "NULL"
        rows = connection.execute(
            "SELECT b.trowel_session_id, b.cc_session_id, b.bound_at,"
            " b.start_offset, s.jsonl_path, s.last_completed_offset,"
            " s.last_completed_at AS session_last_completed_at,"
            f" {status_sql} AS binding_status,"
            f" {completed_sql} AS binding_completed_at"
            " FROM session_bindings AS b JOIN sessions AS s"
            " ON s.cc_session_id = b.cc_session_id"
            " WHERE COALESCE(b.session_kind, 'user') = 'user'"
            " ORDER BY b.cc_session_id, b.bound_at, b.trowel_session_id"
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped[str(row["cc_session_id"])].append(row)

        sources: list[ClaudeBindingSource] = []
        for native_session_id, bindings in grouped.items():
            for index, row in enumerate(bindings):
                session_id = str(row["trowel_session_id"])
                live = active.get(session_id)
                next_bound_at = (
                    str(bindings[index + 1]["bound_at"])
                    if index + 1 < len(bindings)
                    else None
                )
                binding_completed_at = (
                    str(row["binding_completed_at"])
                    if row["binding_completed_at"] is not None
                    else None
                )
                session_completed_at = (
                    str(row["session_last_completed_at"])
                    if row["session_last_completed_at"] is not None
                    else None
                )
                overlap_end = (
                    next_bound_at or binding_completed_at or session_completed_at
                )
                if not any(
                    _source_overlaps_window(
                        row["bound_at"],
                        overlap_end,
                        window,
                        running=bool(live is not None and live.running),
                    )
                    for window in windows
                ):
                    continue
                transcript = Path(str(row["jsonl_path"] or ""))
                try:
                    file_size = transcript.stat().st_size
                except OSError:
                    file_size = 0
                later_starts = [
                    candidate["start_offset"]
                    for candidate in bindings[index + 1 :]
                    if _non_negative_int(candidate["start_offset"])
                ]
                completed_offset = row["last_completed_offset"]
                end_offset = (
                    int(later_starts[0])
                    if later_starts
                    else int(completed_offset)
                    if _non_negative_int(completed_offset)
                    else file_size
                )
                status = (
                    "running"
                    if live is not None and live.running
                    else _non_live_status(row["binding_status"])
                )
                sources.append(
                    ClaudeBindingSource(
                        session_id=session_id,
                        native_session_id=native_session_id,
                        transcript_path=transcript,
                        start_offset=(
                            int(row["start_offset"])
                            if _non_negative_int(row["start_offset"])
                            else None
                        ),
                        end_offset=(
                            min(end_offset, file_size) if file_size else end_offset
                        ),
                        bound_at=str(row["bound_at"]),
                        completed_at=(
                            None
                            if live is not None and live.running
                            else binding_completed_at
                            or (session_completed_at if next_bound_at is None else None)
                        ),
                        status=status,
                        model=live.model if live is not None else None,
                    )
                )
        return sources

    def _codex_sources(
        self,
        connection: sqlite3.Connection,
        active: dict[str, SessionBinding],
        windows: Sequence[StatisticsWindow],
    ) -> list[list[CodexTurnSource]]:
        """读取可能与任一时间窗相交的 Codex turns，并按 session 分组。"""

        rows = connection.execute(
            "SELECT thread_id, turn_id, trowel_session_id, journal_path,"
            " registered_at, completed_at, status, model"
            " FROM codex_turns WHERE session_kind = 'user'"
            " ORDER BY registered_at, thread_id, turn_id"
        ).fetchall()
        grouped: dict[str, list[CodexTurnSource]] = defaultdict(list)
        for row in rows:
            session_id = str(row["trowel_session_id"])
            live = active.get(session_id)
            if not any(
                _source_overlaps_window(
                    row["registered_at"],
                    row["completed_at"],
                    window,
                    running=bool(live is not None and live.running),
                )
                for window in windows
            ):
                continue
            grouped[session_id].append(
                CodexTurnSource(
                    session_id=session_id,
                    native_session_id=str(row["thread_id"]),
                    turn_id=str(row["turn_id"]),
                    journal_path=Path(str(row["journal_path"])),
                    registered_at=str(row["registered_at"]),
                    completed_at=(
                        str(row["completed_at"])
                        if row["completed_at"] is not None
                        else None
                    ),
                    status=(
                        _status(row["status"])
                        if live is not None and live.running
                        else _non_live_status(row["status"])
                    ),
                    model=str(row["model"] or ""),
                )
            )
        source_groups = []
        for session_id, sources in grouped.items():
            live = active.get(session_id)
            if live is not None and live.running:
                last = sources[-1]
                sources[-1] = CodexTurnSource(
                    session_id=last.session_id,
                    native_session_id=last.native_session_id,
                    turn_id=last.turn_id,
                    journal_path=last.journal_path,
                    registered_at=last.registered_at,
                    completed_at=last.completed_at,
                    status="running",
                    model=last.model or live.model or "",
                )
            source_groups.append(sources)
        return source_groups


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """返回 SQLite 表的列名集合。"""

    return {
        str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _source_overlaps_window(
    started_at: object,
    completed_at: object,
    window: StatisticsWindow,
    *,
    running: bool,
) -> bool:
    """用持久时间先排除确定在查询窗外的原生日志。

    Args:
        started_at: binding 或 turn 的登记时间。
        completed_at: 可靠终态时间；旧记录或运行中来源可以缺失。
        window: 当前统计半开时间窗。
        running: binding store 是否确认该 session 当前仍在运行。

    Returns:
        来源可能与时间窗相交时为 True。时间损坏时保守交给 adapter；没有终点的
        旧来源只在窗内开始或仍运行时读取，避免每次查询扫描全部历史。
    """

    start = parse_timestamp(started_at, local_naive=True)
    end = parse_timestamp(completed_at, local_naive=True)
    if start is None:
        return True
    if start >= window.end:
        return False
    if end is not None:
        return end > window.start
    return running or start >= window.start


def _status(value: object) -> SessionStatus:
    """把持久状态收窄到公开集合，未知值降级为 unknown。"""

    raw = str(value or "unknown")
    return cast(SessionStatus, raw if raw in _SESSION_STATUSES else "unknown")


def _non_live_status(value: object) -> SessionStatus:
    """把没有实时 binding 支撑的持久 running 降级为终态未知。"""

    status = _status(value)
    return "unknown" if status == "running" else status


def _non_negative_int(value: object) -> bool:
    """判断 SQLite 值是否是非负整数且不是布尔值。"""

    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
