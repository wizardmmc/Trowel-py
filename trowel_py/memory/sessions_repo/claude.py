"""Claude Code 会话、字节水位与 Trowel 身份绑定的仓储。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .database import row_to_binding, row_to_record
from .models import (
    ClaudePendingSegment,
    ClaudeSessionBinding,
    ClaudeSessionRecord,
    ReviewRequest,
)


class ClaudeSessionsRepository:
    """管理 ``sessions`` 与 ``session_bindings`` 两张 CC 专用表。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """复用 composition root 已初始化的 SQLite 连接。"""
        self._conn = conn

    def register(self, rec: ClaudeSessionRecord) -> None:
        """首次注册保留原记录；有 Trowel ID 时同时落绑定。"""
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
            row = self._conn.execute(
                "SELECT last_completed_offset FROM sessions WHERE cc_session_id = ?",
                (rec.cc_session_id,),
            ).fetchone()
            start_offset = (
                int(row["last_completed_offset"])
                if row is not None and row["last_completed_offset"] is not None
                else 0
            )
            self.bind_session(
                ClaudeSessionBinding(
                    trowel_session_id=rec.trowel_session_id,
                    cc_session_id=rec.cc_session_id,
                    session_kind=rec.session_kind,
                    workdir=rec.workdir,
                    bound_at=rec.registered_at or datetime.now().isoformat(),
                    start_offset=start_offset,
                )
            )
        self._conn.commit()

    def find_pending(
        self,
        date: str,
        exclude_workdir_substr: str = "",
        exclude_kinds: list[str] | None = None,
    ) -> list[ClaudeSessionRecord]:
        """返回指定日期尚未提炼的 CC 会话，按注册时间排序。"""
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

    def find_by_date(self, date: str) -> list[ClaudeSessionRecord]:
        """返回指定日期登记的全部 CC 会话，按登记时间排序。"""
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE date = ? ORDER BY registered_at",
            (date,),
        ).fetchall()
        return [row_to_record(row) for row in rows]

    def mark_extracted(self, cc_session_id: str, when: str) -> None:
        """写入旧式整会话提炼完成时间。"""
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
        """覆盖 CC 会话的完整 turn 字节水位并提交。"""
        stamp = when or datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET last_completed_offset = ?,"
            " last_completed_at = ? WHERE cc_session_id = ?",
            (completed_bytes, stamp, cc_session_id),
        )
        self._conn.commit()

    def list_pending_segments(
        self,
        *,
        completed_before: str | None = None,
    ) -> list[ClaudePendingSegment]:
        """返回用户 CC 会话中尚未提炼的已完成字节区间。"""
        cutoff_sql = ""
        params: list[str] = []
        if completed_before is not None:
            cutoff_sql = " AND last_completed_at < ?"
            params.append(completed_before)
        rows = self._conn.execute(
            "SELECT * FROM sessions"
            " WHERE COALESCE(session_kind, 'user') = 'user'"
            " AND last_completed_offset IS NOT NULL"
            " AND last_completed_offset > COALESCE(last_extracted_offset, 0)"
            + cutoff_sql
            + " ORDER BY registered_at",
            params,
        ).fetchall()
        segments: list[ClaudePendingSegment] = []
        for row in rows:
            record = row_to_record(row)
            start = record.last_extracted_offset or 0
            end = record.last_completed_offset or 0
            if end > start:
                segments.append(ClaudePendingSegment(record, start, end))
        return segments

    def find_pending_for_close_request(
        self,
        request: ReviewRequest,
    ) -> list[ClaudePendingSegment]:
        """返回 CC 关闭请求入队时冻结且仍未提炼的字节范围。"""
        if (
            request.runtime != "claude_code"
            or not request.native_session_id
            or request.source_start_offset is None
            or request.source_end_offset is None
        ):
            return []
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE cc_session_id = ?"
            " AND COALESCE(session_kind, 'user') = 'user'",
            (request.native_session_id,),
        ).fetchone()
        if row is None:
            return []
        record = row_to_record(row)
        extracted = record.last_extracted_offset or 0
        if extracted < request.source_start_offset:
            return []
        completed = record.last_completed_offset or 0
        end = min(completed, request.source_end_offset)
        if end <= extracted:
            return []
        return [ClaudePendingSegment(record, extracted, end)]

    def advance_segment(
        self,
        cc_session_id: str,
        end_offset: int,
        when: str | None = None,
    ) -> None:
        """推进 CC 会话已完整持久化的 transcript 字节水位。"""
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
    ) -> list[ClaudeSessionRecord]:
        """返回已完成的 CC 会话；默认只允许用户会话进入提炼。"""
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

    def bind_session(self, binding: ClaudeSessionBinding) -> None:
        """按 Trowel 会话 ID 幂等落绑定，绝不覆盖首次记录。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO session_bindings"
            " (trowel_session_id, cc_session_id, session_kind, workdir,"
            " bound_at, start_offset, status, completed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                binding.trowel_session_id,
                binding.cc_session_id,
                binding.session_kind,
                binding.workdir,
                binding.bound_at,
                binding.start_offset,
                binding.status,
                binding.completed_at,
            ),
        )
        self._conn.commit()

    def update_binding_status(
        self,
        trowel_session_id: str,
        *,
        status: str,
        completed_at: str | None,
    ) -> None:
        """记录一个 CC binding 的最新 turn 状态。

        Args:
            trowel_session_id: 要更新的 Trowel 会话 ID。
            status: running、completed、interrupted、failed 或 unknown。
            completed_at: 终态记录时间；running 或 unknown 时可以为 None。

        Raises:
            ValueError: status 不属于稳定状态集合。
            KeyError: 找不到对应的 Trowel binding。
        """

        if status not in {"running", "completed", "interrupted", "failed", "unknown"}:
            raise ValueError(f"unknown Claude binding status: {status}")
        cursor = self._conn.execute(
            "UPDATE session_bindings SET status = ?, completed_at = ?"
            " WHERE trowel_session_id = ?",
            (status, completed_at, trowel_session_id),
        )
        if cursor.rowcount != 1:
            self._conn.rollback()
            raise KeyError(trowel_session_id)
        self._conn.commit()

    def find_cc_by_trowel(
        self,
        trowel_session_id: str,
    ) -> ClaudeSessionBinding | None:
        """按 Trowel 会话 ID 返回 CC 绑定。"""
        row = self._conn.execute(
            "SELECT * FROM session_bindings WHERE trowel_session_id = ?",
            (trowel_session_id,),
        ).fetchone()
        return row_to_binding(row) if row is not None else None

    def find_trowels_by_cc(
        self,
        cc_session_id: str,
    ) -> list[ClaudeSessionBinding]:
        """返回同一 CC 会话的全部 Trowel 绑定，按绑定时间排序。"""
        rows = self._conn.execute(
            "SELECT * FROM session_bindings WHERE cc_session_id = ? ORDER BY bound_at",
            (cc_session_id,),
        ).fetchall()
        return [row_to_binding(row) for row in rows]

    def all_bindings(self) -> list[ClaudeSessionBinding]:
        """返回仓储中的全部 CC 身份绑定。"""
        rows = self._conn.execute("SELECT * FROM session_bindings").fetchall()
        return [row_to_binding(row) for row in rows]

    def all_cc_kinds(self) -> dict[str, str]:
        """返回非空 CC 会话 ID 到会话类别的映射。"""
        rows = self._conn.execute(
            "SELECT cc_session_id, COALESCE(session_kind, 'user') FROM sessions"
        ).fetchall()
        return {row[0]: row[1] for row in rows if row[0]}
