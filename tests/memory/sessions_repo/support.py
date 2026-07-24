from __future__ import annotations

import sqlite3

from trowel_py.memory.sessions_repo import (
    SessionBinding,
    SessionRecord,
    SessionsRepository,
    create_sessions_repository,
)


def session_record(**overrides) -> SessionRecord:
    values = {
        "cc_session_id": "abc-1",
        "workdir": "/workspace/project",
        "date": "2026-07-09",
        "jsonl_path": "/sessions/example.jsonl",
        "registered_at": "2026-07-09T10:00:00",
    }
    values.update(overrides)
    return SessionRecord(**values)


def repository() -> SessionsRepository:
    return create_sessions_repository(sqlite3.connect(":memory:"))


def complete(
    repo: SessionsRepository,
    *session_ids: str,
) -> None:
    for session_id in session_ids:
        repo.update_completed(session_id, 1000)


def session_binding(**overrides) -> SessionBinding:
    values = {
        "trowel_session_id": "t1",
        "cc_session_id": "cc-1",
        "session_kind": "user",
        "workdir": "/workspace/project",
        "bound_at": "2026-07-17T10:00:00",
    }
    values.update(overrides)
    return SessionBinding(**values)
