"""关闭会话触发的即时 Memory review 请求持久化测试。"""

from __future__ import annotations

import pytest

from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)


def test_review_request_is_idempotent_and_keeps_enqueue_order(tmp_path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        repo.review_requests.enqueue(
            "session-b",
            runtime="codex",
            requested_at="2026-07-31T10:00:01",
        )
        repo.review_requests.enqueue(
            "session-a",
            runtime="claude_code",
            requested_at="2026-07-31T10:00:00",
        )
        repo.review_requests.enqueue(
            "session-a",
            runtime="claude_code",
            requested_at="2026-07-31T11:00:00",
        )

        requests = repo.review_requests.list_pending()

        assert [
            (
                item.trowel_session_id,
                item.runtime,
                item.requested_at,
                item.native_session_id,
                item.source_start_offset,
                item.source_end_offset,
            )
            for item in requests
        ] == [
            (
                "session-a",
                "claude_code",
                "2026-07-31T10:00:00",
                "",
                None,
                None,
            ),
            ("session-b", "codex", "2026-07-31T10:00:01", "", None, None),
        ]
    finally:
        conn.close()


def test_review_request_survives_reopen_until_completed(tmp_path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.review_requests.enqueue(
        "session-a",
        runtime="claude_code",
        requested_at="2026-07-31T10:00:00",
    )
    conn.close()

    reopened = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(reopened)
        assert repo.review_requests.find("session-a") is not None

        assert repo.review_requests.complete("session-a") is True
        assert repo.review_requests.find("session-a") is None
        assert repo.review_requests.complete("session-a") is False
    finally:
        reopened.close()


def test_cc_review_request_freezes_binding_byte_range(tmp_path) -> None:
    from tests.memory.sessions_repo.support import session_record

    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        repo.claude.register(
            session_record(
                cc_session_id="cc-shared",
                trowel_session_id="session-a",
            )
        )
        repo.claude.update_completed("cc-shared", 2048)

        repo.review_requests.enqueue(
            "session-a",
            runtime="claude_code",
            requested_at="2026-07-31T10:00:00",
        )

        request = repo.review_requests.find("session-a")
        assert request is not None
        assert request.native_session_id == "cc-shared"
        assert request.source_start_offset == 0
        assert request.source_end_offset == 2048
    finally:
        conn.close()


def test_cc_enqueue_rejects_missing_registered_source(tmp_path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)

        with pytest.raises(RuntimeError, match="CC review source is not registered"):
            repo.review_requests.enqueue(
                "session-missing",
                runtime="claude_code",
                requested_at="2026-07-31T10:00:00",
                expected_native_session_id="cc-missing",
            )

        assert repo.review_requests.find("session-missing") is None
    finally:
        conn.close()
