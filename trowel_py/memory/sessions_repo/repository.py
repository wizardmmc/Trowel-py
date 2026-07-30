"""Sessions、增量水位与身份绑定的仓储行为。"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from hashlib import sha256

from .database import (
    ensure_columns,
    initialize_schema,
    row_to_binding,
    row_to_codex_turn,
    row_to_record,
)
from .models import (
    CodexPendingFragment,
    CodexTurnRecord,
    IncrementalSegment,
    SessionBinding,
    SessionRecord,
)


def _codex_fragment_id(turns: list[CodexTurnRecord]) -> str:
    """由确定的 thread 和有序 turn 集合生成稳定片段 ID。"""
    first = turns[0]
    if len(turns) == 1:
        return f"codex:{first.thread_id}:{first.turn_id}"
    digest = sha256(
        "\0".join(turn.turn_id for turn in turns).encode("utf-8")
    ).hexdigest()[:16]
    return f"codex:{first.thread_id}:fragment:{digest}"


class SessionsRepository:
    """持有单个 SQLite connection 的 sessions registry。"""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        migrate: bool = True,
    ) -> None:
        """配置连接，并按需初始化或迁移 sessions schema。

        Args:
            conn: 仓储使用的 SQLite 连接；其 ``row_factory`` 会被覆盖。
            migrate: 是否创建基础表并补齐兼容列；False 时假定 schema 已可用。
        """

        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        if migrate:
            initialize_schema(self._conn)
            self._conn.commit()

    def _ensure_columns(self) -> None:
        """补齐现有 schema 的兼容列和增量索引，不显式提交事务。"""
        ensure_columns(self._conn)

    def register(self, rec: SessionRecord) -> None:
        """首次注册保留原记录；有 trowel id 时同时落绑定。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions"
            " (cc_session_id, workdir, date, jsonl_path, registered_at,"
            " extracted_at, session_kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rec.cc_session_id,
                rec.workdir,
                rec.date,
                rec.jsonl_path,
                rec.registered_at,
                rec.extracted_at,
                rec.session_kind,
            ),
        )
        if rec.trowel_session_id:
            self.bind_session(
                SessionBinding(
                    trowel_session_id=rec.trowel_session_id,
                    cc_session_id=rec.cc_session_id,
                    session_kind=rec.session_kind,
                    workdir=rec.workdir,
                    bound_at=rec.registered_at or datetime.now().isoformat(),
                )
            )
        self._conn.commit()

    def find_pending(
        self,
        date: str,
        exclude_workdir_substr: str = "",
        exclude_kinds: list[str] | None = None,
    ) -> list[SessionRecord]:
        """返回指定日期尚未提炼的 session，按注册时间排序。"""
        clauses = ["date = ?", "extracted_at IS NULL"]
        params: list = [date]
        if exclude_workdir_substr:
            clauses.append("workdir NOT LIKE ?")
            params.append(f"%{exclude_workdir_substr}%")
        if exclude_kinds:
            placeholders = ",".join("?" * len(exclude_kinds))
            clauses.append(f"COALESCE(session_kind, 'user') NOT IN ({placeholders})")
            params.extend(exclude_kinds)
        sql = (
            "SELECT * FROM sessions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY registered_at"
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [row_to_record(row) for row in rows]

    def find_by_date(self, date: str) -> list[SessionRecord]:
        """返回指定日期登记的全部 Claude Code 会话，按登记时间排序。

        本查询不按提炼状态、会话类别或 Memory 资格过滤。
        """

        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE date = ? ORDER BY registered_at",
            (date,),
        ).fetchall()
        return [row_to_record(row) for row in rows]

    def mark_extracted(self, cc_session_id: str, when: str) -> None:
        """写入旧式整会话提炼完成时间。

        找不到 ``cc_session_id`` 时静默提交空更新。

        Args:
            cc_session_id: 要标记的 Claude Code 原生会话 ID。
            when: 要保存的提炼完成时间。
        """

        self._conn.execute(
            "UPDATE sessions SET extracted_at = ? WHERE cc_session_id = ?",
            (when, cc_session_id),
        )
        self._conn.commit()

    def update_completed(
        self,
        cc_session_id: str,
        completed_bytes: int,
        when: str | None = None,
    ) -> None:
        """覆盖会话的完成字节水位并提交。

        本方法不验证字节位置或单调性，也不判断是否位于完整 turn 边界；调用方
        负责只传入可安全提炼的位置。找不到会话时静默提交空更新。

        Args:
            cc_session_id: 要更新的 Claude Code 原生会话 ID。
            completed_bytes: transcript 已完整结束的字节位置。
            when: 水位记录时间；None 时使用本地当前时间。
        """
        stamp = when or datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET last_completed_offset = ?,"
            " last_completed_at = ? WHERE cc_session_id = ?",
            (completed_bytes, stamp, cc_session_id),
        )
        self._conn.commit()

    def find_incremental(
        self, *, completed_before: str | None = None
    ) -> list[IncrementalSegment]:
        """返回 user session 中尚未提炼的已完成区间。"""
        cutoff_sql = ""
        params: tuple[str, ...] = ()
        if completed_before is not None:
            cutoff_sql = " AND last_completed_at < ?"
            params = (completed_before,)
        rows = self._conn.execute(
            "SELECT * FROM sessions"
            " WHERE COALESCE(session_kind, 'user') = 'user'"
            " AND last_completed_offset IS NOT NULL"
            " AND last_completed_offset > COALESCE(last_extracted_offset, 0)"
            + cutoff_sql
            + " ORDER BY registered_at",
            params,
        ).fetchall()
        segments: list[IncrementalSegment] = []
        for row in rows:
            record = row_to_record(row)
            start = (
                record.last_extracted_offset
                if record.last_extracted_offset is not None
                else 0
            )
            end = record.last_completed_offset or 0
            if end > start:
                segments.append(
                    IncrementalSegment(
                        session=record,
                        start=start,
                        end=end,
                    )
                )
        return segments

    def register_codex_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        trowel_session_id: str,
        workdir: str,
        journal_path: str,
        registered_at: str,
        model: str,
        effort: str,
        provider: str,
        memory_enabled: bool,
        profile_enabled: bool,
        session_kind: str = "user",
    ) -> None:
        """首次事件登记 turn；重放同一原生 turn 时不覆盖原始身份。"""

        self._conn.execute(
            "INSERT OR IGNORE INTO codex_turns"
            " (thread_id, turn_id, trowel_session_id, workdir, journal_path,"
            " registered_at, model, effort, provider, memory_enabled,"
            " profile_enabled, session_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                turn_id,
                trowel_session_id,
                workdir,
                journal_path,
                registered_at,
                model,
                effort,
                provider,
                int(memory_enabled),
                int(profile_enabled),
                session_kind,
            ),
        )
        self._conn.commit()

    def complete_codex_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        status: str,
        completed_at: str,
    ) -> None:
        """写入 Codex 原生轮次的终态和完成时间。

        调用方必须先把终态日志同步到磁盘。本方法允许覆盖已有终态；找不到指定
        轮次时抛出 ``KeyError``，且不提交或回滚连接上的事务。

        Args:
            thread_id: Codex 原生 thread ID。
            turn_id: Codex 原生 turn ID。
            status: ``completed``、``interrupted`` 或 ``failed``。
            completed_at: 原生轮次的完成时间。

        Raises:
            ValueError: ``status`` 不是受支持的终态。
            KeyError: 指定轮次尚未登记。
        """

        if status not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"unknown Codex terminal status: {status}")
        cursor = self._conn.execute(
            "UPDATE codex_turns SET status = ?, completed_at = ?"
            " WHERE thread_id = ? AND turn_id = ?",
            (status, completed_at, thread_id, turn_id),
        )
        if cursor.rowcount != 1:
            raise KeyError((thread_id, turn_id))
        self._conn.commit()

    def find_incremental_codex(
        self, *, completed_before: str | None = None
    ) -> list[CodexPendingFragment]:
        """领取并返回尚未提炼的用户 Codex 片段。

        尚未归属片段的合格轮次按 thread 分组，并把本次成员关系写回数据库。
        已归属但提炼失败的轮次保持原片段，不会吸收后来完成的新轮次。结果按
        各片段首个轮次的完成时间、登记时间、thread ID 和 turn ID 排序。

        Args:
            completed_before: 只领取完成时间严格早于该值的新轮次；已有片段仅
                在全部待提炼成员均早于该值时返回。None 表示不设上限。
        """

        pending_rows = self._conn.execute(
            "SELECT * FROM codex_turns"
            " WHERE completed_at IS NOT NULL AND extracted_at IS NULL"
            " AND session_kind = 'user'"
            " ORDER BY completed_at, registered_at, thread_id, turn_id"
        ).fetchall()
        assigned: dict[str, list[CodexTurnRecord]] = defaultdict(list)
        unassigned: dict[str, list[CodexTurnRecord]] = defaultdict(list)
        for row in pending_rows:
            turn = row_to_codex_turn(row)
            if turn.review_fragment_id:
                assigned[turn.review_fragment_id].append(turn)
            elif completed_before is None or (
                turn.completed_at is not None and turn.completed_at < completed_before
            ):
                unassigned[turn.thread_id].append(turn)

        claimed: list[CodexPendingFragment] = []
        for turns in unassigned.values():
            fragment_id = _codex_fragment_id(turns)
            turn_ids = tuple(turn.turn_id for turn in turns)
            placeholders = ",".join("?" for _ in turn_ids)
            self._conn.execute("SAVEPOINT claim_codex_fragment")
            cursor = self._conn.execute(
                "UPDATE codex_turns SET review_fragment_id = ?"
                " WHERE thread_id = ?"
                f" AND turn_id IN ({placeholders})"
                " AND review_fragment_id = ''"
                " AND completed_at IS NOT NULL AND extracted_at IS NULL",
                (fragment_id, turns[0].thread_id, *turn_ids),
            )
            if cursor.rowcount != len(turns):
                self._conn.execute("ROLLBACK TO claim_codex_fragment")
                self._conn.execute("RELEASE claim_codex_fragment")
                raise RuntimeError("Codex fragment claim changed concurrently")
            self._conn.execute("RELEASE claim_codex_fragment")
            assigned_turns = tuple(
                replace(turn, review_fragment_id=fragment_id) for turn in turns
            )
            claimed.append(CodexPendingFragment(fragment_id, assigned_turns))
        if claimed:
            self._conn.commit()

        existing = [
            CodexPendingFragment(fragment_id, tuple(turns))
            for fragment_id, turns in assigned.items()
            if completed_before is None
            or all(
                turn.completed_at is not None and turn.completed_at < completed_before
                for turn in turns
            )
        ]
        fragments = existing + claimed
        fragments.sort(
            key=lambda fragment: (
                fragment.turns[0].completed_at or "",
                fragment.turns[0].registered_at,
                fragment.thread_id,
                fragment.turns[0].turn_id,
            )
        )
        return fragments

    def find_unsealed_codex_turns(self) -> list[CodexTurnRecord]:
        """返回 ``completed_at`` 为空的全部 Codex 轮次，供日志修复使用。

        本查询不按会话类别或当前状态过滤，结果按登记时间、thread ID 和 turn ID
        排序。
        """

        rows = self._conn.execute(
            "SELECT * FROM codex_turns WHERE completed_at IS NULL"
            " ORDER BY registered_at, thread_id, turn_id"
        ).fetchall()
        return [row_to_codex_turn(row) for row in rows]

    def advance_codex_extracted(
        self,
        thread_id: str,
        turn_id: str,
        *,
        when: str | None = None,
    ) -> None:
        """兼容旧入口，并原子推进指定 turn 所属的整个 Codex 片段。

        已领取 fragment 的 turn 会连同该 fragment 的全部待提炼成员一起推进；
        尚未领取的 turn 单独推进。轮次不存在、尚未封口或已经推进时保持旧行为，
        静默提交空更新。

        Args:
            thread_id: Codex 原生 thread ID。
            turn_id: Codex 原生 turn ID。
            when: 提炼完成时间；None 时使用本地当前时间。
        """

        row = self._conn.execute(
            "SELECT review_fragment_id FROM codex_turns"
            " WHERE thread_id = ? AND turn_id = ?"
            " AND completed_at IS NOT NULL AND extracted_at IS NULL",
            (thread_id, turn_id),
        ).fetchone()
        if row is None:
            self._conn.commit()
            return
        fragment_id = row["review_fragment_id"] or ""
        if fragment_id:
            rows = self._conn.execute(
                "SELECT turn_id FROM codex_turns"
                " WHERE thread_id = ? AND review_fragment_id = ?"
                " AND completed_at IS NOT NULL AND extracted_at IS NULL"
                " ORDER BY completed_at, registered_at, turn_id",
                (thread_id, fragment_id),
            ).fetchall()
            included_turn_ids = tuple(str(item["turn_id"]) for item in rows)
        else:
            included_turn_ids = (turn_id,)
        self.advance_codex_extracted_many(
            thread_id,
            included_turn_ids,
            when=when,
        )

    def advance_codex_extracted_many(
        self,
        thread_id: str,
        turn_ids: tuple[str, ...],
        *,
        when: str | None = None,
    ) -> None:
        """在一个 SQLite 事务中推进一个 Codex 片段的全部 turn 水位。

        只有同属 ``thread_id``、已经封口且尚未推进的全部 turn 都存在时才提交；
        任一 turn 不符合条件会撤销本次全部更新。

        Raises:
            ValueError: turn 列表为空、重复，或无法完整原子更新。
        """

        if not turn_ids or len(turn_ids) != len(set(turn_ids)):
            raise ValueError("Codex atomic advance requires unique turn ids")
        stamp = when or datetime.now().isoformat()
        placeholders = ",".join("?" for _ in turn_ids)
        self._conn.execute("SAVEPOINT advance_codex_fragment")
        rows = self._conn.execute(
            "SELECT review_fragment_id FROM codex_turns"
            " WHERE thread_id = ?"
            f" AND turn_id IN ({placeholders})"
            " AND completed_at IS NOT NULL AND extracted_at IS NULL",
            (thread_id, *turn_ids),
        ).fetchall()
        fragment_ids = {str(row["review_fragment_id"] or "") for row in rows}
        if len(rows) != len(turn_ids) or len(fragment_ids) != 1:
            self._conn.execute("ROLLBACK TO advance_codex_fragment")
            self._conn.execute("RELEASE advance_codex_fragment")
            raise ValueError("Codex fragment could not advance atomically")
        cursor = self._conn.execute(
            "UPDATE codex_turns SET extracted_at = ?"
            " WHERE thread_id = ?"
            f" AND turn_id IN ({placeholders})"
            " AND completed_at IS NOT NULL AND extracted_at IS NULL",
            (stamp, thread_id, *turn_ids),
        )
        if cursor.rowcount != len(turn_ids):
            self._conn.execute("ROLLBACK TO advance_codex_fragment")
            self._conn.execute("RELEASE advance_codex_fragment")
            raise ValueError("Codex fragment could not advance atomically")
        self._conn.execute("RELEASE advance_codex_fragment")
        self._conn.commit()

    def advance_extracted(
        self,
        cc_session_id: str,
        end_offset: int,
        when: str | None = None,
    ) -> None:
        """覆盖 Claude Code 会话的提炼字节水位并提交。

        本方法不验证字节位置或单调性；找不到会话时静默提交空更新。

        Args:
            cc_session_id: 要更新的 Claude Code 原生会话 ID。
            end_offset: 已成功持久化到 Memory 的 transcript 结束位置。
            when: 水位记录时间；None 时使用本地当前时间。
        """

        stamp = when or datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET last_extracted_offset = ?,"
            " last_extracted_at = ? WHERE cc_session_id = ?",
            (end_offset, stamp, cc_session_id),
        )
        self._conn.commit()

    def find_all_completed_sessions(
        self,
        exclude_kinds: list[str] | None = None,
    ) -> list[SessionRecord]:
        """返回已完成 session；默认只允许用户会话进入提炼。"""
        if exclude_kinds is None:
            where_kind = "COALESCE(session_kind, 'user') = 'user'"
            params: list[str] = []
        else:
            placeholders = ",".join("?" * len(exclude_kinds))
            where_kind = f"COALESCE(session_kind, 'user') NOT IN ({placeholders})"
            params = exclude_kinds
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE "
            + where_kind
            + " AND last_completed_offset IS NOT NULL ORDER BY registered_at",
            params,
        ).fetchall()
        return [row_to_record(row) for row in rows]

    def bind_session(self, binding: SessionBinding) -> None:
        """按 trowel session id 幂等落绑定，绝不覆盖首次记录。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO session_bindings"
            " (trowel_session_id, cc_session_id, session_kind, workdir,"
            " bound_at) VALUES (?, ?, ?, ?, ?)",
            (
                binding.trowel_session_id,
                binding.cc_session_id,
                binding.session_kind,
                binding.workdir,
                binding.bound_at,
            ),
        )
        self._conn.commit()

    def find_cc_by_trowel(
        self,
        trowel_session_id: str,
    ) -> SessionBinding | None:
        """按 Trowel 会话 ID 返回绑定，未找到时返回 None。"""

        row = self._conn.execute(
            "SELECT * FROM session_bindings WHERE trowel_session_id = ?",
            (trowel_session_id,),
        ).fetchone()
        return row_to_binding(row) if row is not None else None

    def find_trowels_by_cc(
        self,
        cc_session_id: str,
    ) -> list[SessionBinding]:
        """返回同一 Claude Code 会话的全部绑定，按绑定时间排序。"""

        rows = self._conn.execute(
            "SELECT * FROM session_bindings WHERE cc_session_id = ? ORDER BY bound_at",
            (cc_session_id,),
        ).fetchall()
        return [row_to_binding(row) for row in rows]

    def all_bindings(self) -> list[SessionBinding]:
        """返回仓储中的全部会话绑定，不保证顺序。"""

        rows = self._conn.execute("SELECT * FROM session_bindings").fetchall()
        return [row_to_binding(row) for row in rows]

    def all_cc_kinds(self) -> dict[str, str]:
        """返回非空 Claude Code 会话 ID 到会话类别的映射。

        旧记录的 ``NULL`` 类别按 ``user`` 返回。
        """

        rows = self._conn.execute(
            "SELECT cc_session_id, COALESCE(session_kind, 'user') FROM sessions"
        ).fetchall()
        return {row[0]: row[1] for row in rows if row[0]}


def create_sessions_repository(
    conn: sqlite3.Connection,
    *,
    migrate: bool = True,
) -> SessionsRepository:
    """用调用方提供的 SQLite 连接创建 sessions 仓储。

    Args:
        conn: 仓储使用的连接，仍由调用方关闭。
        migrate: 是否在创建仓储时初始化和迁移 schema。

    Returns:
        共享该连接的 ``SessionsRepository``。
    """

    return SessionsRepository(conn, migrate=migrate)
