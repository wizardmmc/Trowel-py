"""Sessions、增量水位与身份绑定的仓储行为。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .database import (
    ensure_columns,
    initialize_schema,
    row_to_binding,
    row_to_record,
)
from .models import IncrementalSegment, SessionBinding, SessionRecord


class SessionsRepository:
    """持有单个 SQLite connection 的 sessions registry。"""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        migrate: bool = True,
    ) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        if migrate:
            initialize_schema(self._conn)
            self._conn.commit()

    def _ensure_columns(self) -> None:
        """兼容仍显式调用旧私有迁移入口的代码。"""
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
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE date = ? ORDER BY registered_at",
            (date,),
        ).fetchall()
        return [row_to_record(row) for row in rows]

    def mark_extracted(self, cc_session_id: str, when: str) -> None:
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
        """只在完整 turn 边界推进可安全提炼的字节水位。"""
        stamp = when or datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET last_completed_offset = ?,"
            " last_completed_at = ? WHERE cc_session_id = ?",
            (completed_bytes, stamp, cc_session_id),
        )
        self._conn.commit()

    def find_incremental(self) -> list[IncrementalSegment]:
        """返回 user session 中尚未提炼的已完成区间。"""
        rows = self._conn.execute(
            "SELECT * FROM sessions"
            " WHERE COALESCE(session_kind, 'user') = 'user'"
            " AND last_completed_offset IS NOT NULL"
            " AND last_completed_offset > COALESCE(last_extracted_offset, 0)"
            " ORDER BY registered_at"
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

    def advance_extracted(
        self,
        cc_session_id: str,
        end_offset: int,
        when: str | None = None,
    ) -> None:
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
        """返回全部已完成 session，不受 review 提炼水位影响。"""
        excluded = exclude_kinds if exclude_kinds is not None else ["review", "distill"]
        placeholders = ",".join("?" * len(excluded))
        rows = self._conn.execute(
            "SELECT * FROM sessions"
            " WHERE COALESCE(session_kind, 'user') NOT IN ({placeholders})"
            " AND last_completed_offset IS NOT NULL"
            " ORDER BY registered_at".format(placeholders=placeholders),
            excluded,
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
        row = self._conn.execute(
            "SELECT * FROM session_bindings WHERE trowel_session_id = ?",
            (trowel_session_id,),
        ).fetchone()
        return row_to_binding(row) if row is not None else None

    def find_trowels_by_cc(
        self,
        cc_session_id: str,
    ) -> list[SessionBinding]:
        rows = self._conn.execute(
            "SELECT * FROM session_bindings WHERE cc_session_id = ? ORDER BY bound_at",
            (cc_session_id,),
        ).fetchall()
        return [row_to_binding(row) for row in rows]

    def all_bindings(self) -> list[SessionBinding]:
        rows = self._conn.execute("SELECT * FROM session_bindings").fetchall()
        return [row_to_binding(row) for row in rows]

    def all_cc_kinds(self) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT cc_session_id, COALESCE(session_kind, 'user') FROM sessions"
        ).fetchall()
        return {row[0]: row[1] for row in rows if row[0]}


def create_sessions_repository(
    conn: sqlite3.Connection,
    *,
    migrate: bool = True,
) -> SessionsRepository:
    return SessionsRepository(conn, migrate=migrate)
