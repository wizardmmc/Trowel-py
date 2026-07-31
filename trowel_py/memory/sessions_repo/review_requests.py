"""用户关闭会话触发的持久化 Review 请求队列。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .database import row_to_review_request
from .models import ReviewRequest


class ReviewRequestsRepository:
    """管理关闭请求的入队、查询和完成判定。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """复用 composition root 已初始化的 SQLite 连接。"""
        self._conn = conn

    def enqueue(
        self,
        trowel_session_id: str,
        *,
        runtime: str,
        requested_at: str | None = None,
        expected_native_session_id: str | None = None,
    ) -> None:
        """幂等登记用户关闭会话触发的即时 review 请求。

        CC 请求在入队时冻结绑定的原生会话、首次绑定水位和当前完成水位；
        Codex 请求按 Trowel 会话 ID 在领取阶段选择 turns。
        """
        if not trowel_session_id:
            raise ValueError("review request requires a Trowel session id")
        if runtime not in {"claude_code", "codex"}:
            raise ValueError(f"unknown review request runtime: {runtime}")
        stamp = requested_at or datetime.now().isoformat(timespec="microseconds")
        native_session_id = ""
        source_start_offset: int | None = None
        source_end_offset: int | None = None
        if runtime == "claude_code":
            row = self._conn.execute(
                "SELECT binding.cc_session_id, binding.start_offset,"
                " sessions.last_completed_offset"
                " FROM session_bindings AS binding"
                " LEFT JOIN sessions"
                " ON sessions.cc_session_id = binding.cc_session_id"
                " WHERE binding.trowel_session_id = ?",
                (trowel_session_id,),
            ).fetchone()
            if expected_native_session_id is not None and (
                row is None or row["cc_session_id"] != expected_native_session_id
            ):
                raise RuntimeError(
                    "CC review source is not registered for "
                    f"{trowel_session_id} ({expected_native_session_id})"
                )
            if row is not None:
                native_session_id = str(row["cc_session_id"])
                source_start_offset = row["start_offset"]
                source_end_offset = row["last_completed_offset"]
        self._conn.execute(
            "INSERT OR IGNORE INTO session_review_requests"
            " (trowel_session_id, runtime, requested_at, native_session_id,"
            " source_start_offset, source_end_offset) VALUES (?, ?, ?, ?, ?, ?)",
            (
                trowel_session_id,
                runtime,
                stamp,
                native_session_id,
                source_start_offset,
                source_end_offset,
            ),
        )
        self._conn.commit()

    def find(self, trowel_session_id: str) -> ReviewRequest | None:
        """按 Trowel 会话 ID 读取尚未完成的即时 review 请求。"""
        row = self._conn.execute(
            "SELECT * FROM session_review_requests WHERE trowel_session_id = ?",
            (trowel_session_id,),
        ).fetchone()
        return row_to_review_request(row) if row is not None else None

    def list_pending(self) -> list[ReviewRequest]:
        """按首次入队时间返回全部尚未完成的即时 review 请求。"""
        rows = self._conn.execute(
            "SELECT * FROM session_review_requests"
            " ORDER BY requested_at, trowel_session_id"
        ).fetchall()
        return [row_to_review_request(row) for row in rows]

    def complete(self, trowel_session_id: str) -> bool:
        """删除指定即时 review 请求并报告是否存在。"""
        cursor = self._conn.execute(
            "DELETE FROM session_review_requests WHERE trowel_session_id = ?",
            (trowel_session_id,),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def has_pending_source(self, request: ReviewRequest) -> bool:
        """判断一个关闭请求是否仍有已封口但尚未提炼的来源。"""
        if request.runtime == "claude_code":
            if not request.native_session_id:
                return False
            row = self._conn.execute(
                "SELECT last_completed_offset, last_extracted_offset"
                " FROM sessions WHERE cc_session_id = ?"
                " AND COALESCE(session_kind, 'user') = 'user'",
                (request.native_session_id,),
            ).fetchone()
            if row is None or row["last_completed_offset"] is None:
                return False
            completed = int(row["last_completed_offset"])
            extracted = int(row["last_extracted_offset"] or 0)
            if request.source_start_offset is None:
                return completed > extracted
            if request.source_end_offset is None:
                return False
            return min(completed, request.source_end_offset) > max(
                extracted,
                request.source_start_offset,
            )
        if request.runtime == "codex":
            row = self._conn.execute(
                "SELECT 1 FROM codex_turns"
                " WHERE trowel_session_id = ?"
                " AND session_kind = 'user'"
                " AND completed_at IS NOT NULL AND extracted_at IS NULL"
                " LIMIT 1",
                (request.trowel_session_id,),
            ).fetchone()
            return row is not None
        return True

    def complete_satisfied(
        self,
        *,
        trowel_session_id: str | None = None,
    ) -> tuple[str, ...]:
        """清除已经没有待提炼来源的关闭请求。"""
        if trowel_session_id is None:
            requests = self.list_pending()
        else:
            request = self.find(trowel_session_id)
            requests = [] if request is None else [request]
        completed = tuple(
            request.trowel_session_id
            for request in requests
            if not self.has_pending_source(request)
        )
        if completed:
            placeholders = ",".join("?" for _ in completed)
            self._conn.execute(
                "DELETE FROM session_review_requests"
                f" WHERE trowel_session_id IN ({placeholders})",
                completed,
            )
            self._conn.commit()
        return completed
