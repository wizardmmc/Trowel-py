"""以稳定游标只读查询 Memory sessions.db 中的会话问题。"""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from trowel_py.memory.sessions_repo.database import open_sessions_db_readonly
from trowel_py.statistics.window import StatisticsWindow

from .models import StoredSessionProblem, StoredSessionProblemPage

_CURSOR_VERSION = 1


class FileSessionProblemStatisticsReader:
    """为每次查询打开一个短生命周期只读连接。"""

    def __init__(self, memory_root: Path | str) -> None:
        """保存 sessions.db 所在的 Memory 根目录。"""

        self._memory_root = Path(memory_root)

    def list_page(
        self,
        window: StatisticsWindow,
        *,
        limit: int,
        cursor: str | None,
    ) -> StoredSessionProblemPage:
        """读取时间窗汇总和按关闭时间倒序排列的一页非空问题。"""

        cursor_value = _decode_cursor(cursor) if cursor is not None else None
        connection = open_sessions_db_readonly(self._memory_root)
        if connection is None:
            return _empty_page()
        try:
            if not _has_problem_table(connection):
                return _empty_page()
            return _read_page(
                connection,
                window,
                limit=limit,
                cursor=cursor_value,
            )
        finally:
            connection.close()


def _read_page(
    connection: sqlite3.Connection,
    window: StatisticsWindow,
    *,
    limit: int,
    cursor: tuple[int, str] | None,
) -> StoredSessionProblemPage:
    """在一个 SQLite 只读快照中读取总数、质量和当前页。"""

    start_us = _epoch_microseconds(window.start)
    end_us = _epoch_microseconds(window.end)
    summary = connection.execute(
        "SELECT COUNT(*) AS reviewed_session_count,"
        " COUNT(problem_text) AS problem_count,"
        " SUM(CASE WHEN source_quality != 'reliable' THEN 1 ELSE 0 END)"
        " AS unavailable_source_count,"
        " MAX(reviewed_at) AS updated_at"
        " FROM session_review_problems"
        " WHERE closed_at_epoch_us >= ? AND closed_at_epoch_us < ?",
        (start_us, end_us),
    ).fetchone()
    conditions = [
        "closed_at_epoch_us >= ?",
        "closed_at_epoch_us < ?",
        "problem_text IS NOT NULL",
    ]
    parameters: list[object] = [start_us, end_us]
    if cursor is not None:
        cursor_epoch_us, cursor_session_id = cursor
        conditions.append(
            "(closed_at_epoch_us < ? OR"
            " (closed_at_epoch_us = ? AND trowel_session_id < ?))"
        )
        parameters.extend((cursor_epoch_us, cursor_epoch_us, cursor_session_id))
    parameters.append(limit + 1)
    rows = connection.execute(
        "SELECT trowel_session_id, runtime, closed_at, closed_at_epoch_us,"
        " problem_text FROM session_review_problems"
        f" WHERE {' AND '.join(conditions)}"
        " ORDER BY closed_at_epoch_us DESC, trowel_session_id DESC LIMIT ?",
        parameters,
    ).fetchall()
    has_more = len(rows) > limit
    items = tuple(_stored_problem(row) for row in rows[:limit])
    next_cursor = (
        _encode_cursor(items[-1].closed_at_epoch_us, items[-1].trowel_session_id)
        if has_more and items
        else None
    )
    return StoredSessionProblemPage(
        items=items,
        next_cursor=next_cursor,
        reviewed_session_count=int(summary["reviewed_session_count"]),
        problem_count=int(summary["problem_count"]),
        unavailable_source_count=int(summary["unavailable_source_count"] or 0),
        updated_at=summary["updated_at"],
    )


def _stored_problem(row: sqlite3.Row) -> StoredSessionProblem:
    """只复制公开列表和稳定游标需要的字段。"""

    return StoredSessionProblem(
        trowel_session_id=str(row["trowel_session_id"]),
        runtime=str(row["runtime"]),
        closed_at=str(row["closed_at"]),
        closed_at_epoch_us=int(row["closed_at_epoch_us"]),
        problem_text=str(row["problem_text"]),
    )


def _has_problem_table(connection: sqlite3.Connection) -> bool:
    """让升级前的只读数据库表现为空来源，而不是触发写迁移。"""

    row = connection.execute(
        "SELECT 1 FROM sqlite_master"
        " WHERE type = 'table' AND name = 'session_review_problems'"
    ).fetchone()
    return row is not None


def _empty_page() -> StoredSessionProblemPage:
    """返回不存在数据库或升级前数据库的空快照。"""

    return StoredSessionProblemPage(
        items=(),
        next_cursor=None,
        reviewed_session_count=0,
        problem_count=0,
        unavailable_source_count=0,
        updated_at=None,
    )


def _epoch_microseconds(value: datetime) -> int:
    """把带时区时间转换成 SQLite 排序使用的 Unix 微秒。"""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("statistics window timestamps require a UTC offset")
    return int(value.timestamp() * 1_000_000)


def _encode_cursor(closed_at_epoch_us: int, trowel_session_id: str) -> str:
    """编码不含正文的版本化稳定游标。"""

    payload = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "closed_at_epoch_us": closed_at_epoch_us,
            "trowel_session_id": trowel_session_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[int, str]:
    """严格解码版本化游标，拒绝额外字段和空会话身份。"""

    if not value or len(value) > 2048:
        raise ValueError("invalid session problem cursor")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "v",
            "closed_at_epoch_us",
            "trowel_session_id",
        }:
            raise ValueError
        epoch_us = payload["closed_at_epoch_us"]
        session_id = payload["trowel_session_id"]
        if (
            payload["v"] != _CURSOR_VERSION
            or isinstance(epoch_us, bool)
            or not isinstance(epoch_us, int)
            or not isinstance(session_id, str)
            or not session_id
        ):
            raise ValueError
        return epoch_us, session_id
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid session problem cursor") from exc
