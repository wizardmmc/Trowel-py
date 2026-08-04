"""验证双 runtime 会话问题只读取当前 Trowel 会话的完整来源。"""

from __future__ import annotations

import shutil
from pathlib import Path

from trowel_py.memory.daily_review.problems import build_session_problem_scope
from trowel_py.memory.sessions_repo import (
    SessionBinding,
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)

FIXTURES = Path(__file__).parents[2] / "statistics" / "fixtures"


def test_claude_scope_uses_frozen_trowel_binding_range(tmp_path: Path) -> None:
    """恢复同一原生会话后的新增字节不能进入前一个 Trowel 会话。"""

    source = FIXTURES / "cc-binding-2.1.197.jsonl"
    first_line_end = len(source.read_bytes().splitlines(keepends=True)[0])
    close_end = source.stat().st_size
    connection = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(connection)
        repo.claude.register(
            SessionRecord(
                cc_session_id="native-shared",
                workdir="/isolated/project",
                date="2026-08-03",
                jsonl_path=str(source),
                registered_at="2026-08-03T11:00:00",
            )
        )
        repo.claude.bind_session(
            SessionBinding(
                trowel_session_id="agent-a",
                cc_session_id="native-shared",
                session_kind="user",
                workdir="/isolated/project",
                bound_at="2026-08-03T11:00:00",
                start_offset=first_line_end,
            )
        )
        repo.claude.update_completed("native-shared", close_end)
        repo.review_requests.enqueue(
            "agent-a",
            runtime="claude_code",
            requested_at="2026-08-03T12:00:00",
            closed_at="2026-08-03T12:00:00+08:00",
            expected_native_session_id="native-shared",
        )
        request = repo.review_requests.find("agent-a")
        assert request is not None

        scope = build_session_problem_scope(repo, request)

        assert scope.source_quality == "reliable"
        assert scope.review_source is not None
        assert scope.review_source.context == ()
        assert len(scope.review_source.target) == 1
        target = scope.review_source.target[0]
        assert (target.path, target.start_offset, target.end_offset) == (
            str(source),
            first_line_end,
            close_end,
        )
    finally:
        connection.close()


def test_codex_scope_keeps_only_current_trowel_turns_in_completion_order(
    tmp_path: Path,
) -> None:
    """同一 thread 中属于另一个 Trowel 会话的 turn 不能混入问题来源。"""

    recorded = FIXTURES / "codex-turn-0.144.0.jsonl"
    journals = tmp_path / "journals"
    journals.mkdir()
    first = journals / "first.jsonl"
    second = journals / "second.jsonl"
    other = journals / "other.jsonl"
    for target in (first, second, other):
        shutil.copyfile(recorded, target)
    connection = open_sessions_db(tmp_path / "memory")
    try:
        repo = create_sessions_repository(connection)
        rows = (
            ("turn-2", "agent-a", second, "2026-08-03T12:02:00+08:00"),
            ("turn-other", "agent-b", other, "2026-08-03T12:01:00+08:00"),
            ("turn-1", "agent-a", first, "2026-08-03T12:00:00+08:00"),
        )
        for turn_id, session_id, journal, completed_at in rows:
            repo.codex.register_turn(
                thread_id="thread-shared",
                turn_id=turn_id,
                trowel_session_id=session_id,
                workdir="/isolated/project",
                journal_path=str(journal),
                registered_at=completed_at,
                model="gpt-5.6",
                effort="high",
                provider="openai",
                memory_enabled=True,
                profile_enabled=True,
            )
            repo.codex.complete_turn(
                "thread-shared",
                turn_id,
                status="completed",
                completed_at=completed_at,
            )
        repo.review_requests.enqueue(
            "agent-a",
            runtime="codex",
            requested_at="2026-08-03T12:03:00",
            closed_at="2026-08-03T12:03:00+08:00",
        )
        request = repo.review_requests.find("agent-a")
        assert request is not None

        scope = build_session_problem_scope(repo, request)

        assert scope.review_source is not None
        assert [Path(item.path).name for item in scope.review_source.target] == [
            "first.jsonl",
            "second.jsonl",
        ]
        assert scope.native_session_ids == ("thread-shared",)
    finally:
        connection.close()
