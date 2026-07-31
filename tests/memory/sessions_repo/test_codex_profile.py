"""Codex Profile 候选查询的仓储测试。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)


def _register(
    root: Path,
    *,
    thread_id: str,
    turn_id: str,
    registered_at: str,
    completed_at: str | None,
    session_kind: str = "user",
    profile_enabled: bool = True,
) -> None:
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.codex.register_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            trowel_session_id=f"trowel-{turn_id}",
            workdir="/workspace",
            journal_path=f"/journals/{turn_id}.jsonl",
            registered_at=registered_at,
            model="gpt-5.6-sol",
            effort="high",
            provider="openai",
            memory_enabled=True,
            profile_enabled=profile_enabled,
            session_kind=session_kind,
        )
        if completed_at is not None:
            repo.codex.complete_turn(
                thread_id,
                turn_id,
                status="completed",
                completed_at=completed_at,
            )
    finally:
        conn.close()


def test_list_completed_user_turns_excludes_running_and_internal(
    tmp_path: Path,
) -> None:
    _register(
        tmp_path,
        thread_id="thread-a",
        turn_id="completed-user",
        registered_at="2026-07-31T10:00:00",
        completed_at="2026-07-31T10:05:00",
    )
    _register(
        tmp_path,
        thread_id="thread-a",
        turn_id="running-user",
        registered_at="2026-07-31T10:01:00",
        completed_at=None,
    )
    _register(
        tmp_path,
        thread_id="thread-b",
        turn_id="completed-distill",
        registered_at="2026-07-31T10:02:00",
        completed_at="2026-07-31T10:03:00",
        session_kind="distill",
    )

    conn = open_sessions_db(tmp_path)
    try:
        turns = create_sessions_repository(conn).codex.list_completed_user_turns()
    finally:
        conn.close()

    assert [turn.turn_id for turn in turns] == ["completed-user"]


def test_list_completed_user_turns_ignores_other_pipeline_watermarks_and_switch(
    tmp_path: Path,
) -> None:
    _register(
        tmp_path,
        thread_id="thread-a",
        turn_id="turn-1",
        registered_at="2026-07-31T10:00:00",
        completed_at="2026-07-31T10:05:00",
        profile_enabled=False,
    )
    conn = open_sessions_db(tmp_path)
    try:
        conn.execute(
            "UPDATE codex_turns SET extracted_at = '2026-07-31T11:00:00',"
            " review_fragment_id = 'memory-fragment'"
            " WHERE thread_id = 'thread-a' AND turn_id = 'turn-1'"
        )
        conn.commit()
        turns = create_sessions_repository(conn).codex.list_completed_user_turns()
    finally:
        conn.close()

    assert [turn.turn_id for turn in turns] == ["turn-1"]


def test_list_completed_user_turns_has_stable_completion_order(
    tmp_path: Path,
) -> None:
    _register(
        tmp_path,
        thread_id="thread-b",
        turn_id="turn-b",
        registered_at="2026-07-31T10:01:00",
        completed_at="2026-07-31T10:05:00",
    )
    _register(
        tmp_path,
        thread_id="thread-a",
        turn_id="turn-a2",
        registered_at="2026-07-31T10:00:00",
        completed_at="2026-07-31T10:05:00",
    )
    _register(
        tmp_path,
        thread_id="thread-a",
        turn_id="turn-a1",
        registered_at="2026-07-31T09:59:00",
        completed_at="2026-07-31T10:04:00",
    )

    conn = open_sessions_db(tmp_path)
    try:
        turns = create_sessions_repository(conn).codex.list_completed_user_turns()
    finally:
        conn.close()

    assert [turn.turn_id for turn in turns] == ["turn-a1", "turn-a2", "turn-b"]
