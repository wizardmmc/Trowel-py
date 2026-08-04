"""持久保存一会话一条的复盘问题完成记录。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .database import row_to_session_problem
from .models import SessionProblemRecord

_MAX_PROBLEM_CHARS = 2000


def _epoch_microseconds(value: str) -> int:
    """把带 UTC 偏移的 ISO 时间转换为排序使用的 Unix 微秒。"""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("session problem timestamps must include a UTC offset")
    return int(parsed.timestamp() * 1_000_000)


def _validate_record(record: SessionProblemRecord) -> None:
    """拒绝不完整身份、非法枚举和伪空白问题。"""

    if not record.trowel_session_id:
        raise ValueError("session problem requires a Trowel session id")
    if record.runtime not in {"claude_code", "codex"}:
        raise ValueError(f"unknown session problem runtime: {record.runtime}")
    if record.problem_text is not None and not record.problem_text.strip():
        raise ValueError("session problem text must not be blank")
    if (
        record.problem_text is not None
        and len(record.problem_text) > _MAX_PROBLEM_CHARS
    ):
        raise ValueError("session problem text is too long")
    if record.problem_text is not None and "\x00" in record.problem_text:
        raise ValueError("session problem text must not contain NUL")
    if record.pipeline_version < 1:
        raise ValueError("session problem pipeline version must be positive")
    if record.source_quality not in {"reliable", "unavailable"}:
        raise ValueError("session problem source quality is invalid")
    _epoch_microseconds(record.closed_at)
    _epoch_microseconds(record.reviewed_at)


class SessionProblemsRepository:
    """管理会话问题记录及其关闭请求完成标记。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """复用 sessions registry 已初始化的 SQLite 连接。"""

        self._conn = conn

    def save_completed(self, record: SessionProblemRecord) -> bool:
        """原子保存首次完成结果，并标记对应关闭请求已记录问题。

        Args:
            record: 已校验来源并完成模型分析或确定性空判的会话问题。

        Returns:
            本次首次创建记录时为 True，幂等重试命中已有记录时为 False。

        Raises:
            KeyError: 对应关闭请求已不存在。
            ValueError: 记录身份、时间、枚举或文本不符合契约。
            sqlite3.Error: 原子事务无法提交。
        """

        _validate_record(record)
        closed_epoch_us = _epoch_microseconds(record.closed_at)
        self._conn.execute("SAVEPOINT save_session_problem")
        try:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO session_review_problems"
                " (trowel_session_id, runtime, closed_at, closed_at_epoch_us,"
                " problem_text, reviewed_at, pipeline_version, run_id,"
                " generator_runtime, generator_model, generator_effort,"
                " source_quality) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.trowel_session_id,
                    record.runtime,
                    record.closed_at,
                    closed_epoch_us,
                    record.problem_text,
                    record.reviewed_at,
                    record.pipeline_version,
                    record.run_id,
                    record.generator_runtime,
                    record.generator_model,
                    record.generator_effort,
                    record.source_quality,
                ),
            )
            request_cursor = self._conn.execute(
                "UPDATE session_review_requests"
                " SET problem_recorded_at = COALESCE(problem_recorded_at, ?)"
                " WHERE trowel_session_id = ?",
                (record.reviewed_at, record.trowel_session_id),
            )
            if request_cursor.rowcount != 1:
                raise KeyError(record.trowel_session_id)
            self._conn.execute("RELEASE save_session_problem")
        except Exception:
            self._conn.execute("ROLLBACK TO save_session_problem")
            self._conn.execute("RELEASE save_session_problem")
            self._conn.rollback()
            raise
        try:
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return cursor.rowcount == 1

    def find(self, trowel_session_id: str) -> SessionProblemRecord | None:
        """按 Trowel 会话 ID 读取完成结果，包括明确空结果。"""

        row = self._conn.execute(
            "SELECT * FROM session_review_problems WHERE trowel_session_id = ?",
            (trowel_session_id,),
        ).fetchone()
        return row_to_session_problem(row) if row is not None else None

    def list_all(self) -> list[SessionProblemRecord]:
        """按关闭时间和会话 ID 正序返回全部完成记录。"""

        rows = self._conn.execute(
            "SELECT * FROM session_review_problems"
            " ORDER BY closed_at_epoch_us, trowel_session_id"
        ).fetchall()
        return [row_to_session_problem(row) for row in rows]
