"""Memory CLI 测试数据。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)


def seed_legacy_session(memory_root: Path, session_id: str, jsonl_path: str) -> None:
    """登记一条没有完成水位的旧会话。"""
    conn = open_sessions_db(memory_root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id=session_id,
                workdir="/project",
                date="2026-07-09",
                jsonl_path=jsonl_path,
                registered_at="2026-07-09T10:00:00",
            )
        )
    finally:
        conn.close()


def completed_offset(memory_root: Path, session_id: str) -> int | None:
    conn = open_sessions_db(memory_root)
    try:
        for record in create_sessions_repository(conn).find_by_date("2026-07-09"):
            if record.cc_session_id == session_id:
                return record.last_completed_offset
    finally:
        conn.close()
    return None


def seed_user_session(memory_root: Path, session_id: str, jsonl_path: str) -> None:
    """登记一条已完成的用户会话。"""
    conn = open_sessions_db(memory_root)
    try:
        repository = create_sessions_repository(conn)
        repository.register(
            SessionRecord(
                cc_session_id=session_id,
                workdir="/project",
                date="2026-07-14",
                jsonl_path=jsonl_path,
                registered_at="2026-07-14T10:00:00",
            )
        )
        repository.update_completed(session_id, 500)
    finally:
        conn.close()
