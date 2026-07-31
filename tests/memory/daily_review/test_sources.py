from __future__ import annotations

from typing import cast

from trowel_py.memory.daily_review.sources import (
    JournalSlice,
    ReviewSource,
    build_claude_review_source,
    build_codex_review_source,
    render_review_source,
)
from trowel_py.memory.sessions_repo import (
    CodexPendingFragment,
    CodexTurnsRepository,
    CodexTurnRecord,
    IncrementalSegment,
    SessionRecord,
)


def test_claude_source_exposes_prefix_as_context_and_increment_as_target() -> None:
    session = SessionRecord(
        cc_session_id="cc-1",
        workdir="/workspace",
        date="2026-07-30",
        jsonl_path="/journals/cc-1.jsonl",
        registered_at="2026-07-30T10:00:00",
    )

    source = build_claude_review_source(
        IncrementalSegment(session=session, start=100, end=250)
    )

    assert source == ReviewSource(
        host_kind="claude_code",
        context=(JournalSlice("/journals/cc-1.jsonl", 0, 100),),
        target=(JournalSlice("/journals/cc-1.jsonl", 100, 250),),
    )


def test_codex_source_exposes_extracted_turns_only_as_context() -> None:
    history = CodexTurnRecord(
        thread_id="thread-1",
        turn_id="turn-history",
        trowel_session_id="trowel-history",
        workdir="/workspace",
        journal_path="/journals/history.jsonl",
        registered_at="2026-07-30T09:00:00",
        completed_at="2026-07-30T09:05:00",
        extracted_at="2026-07-30T09:10:00",
    )
    target = CodexTurnRecord(
        thread_id="thread-1",
        turn_id="turn-target",
        trowel_session_id="trowel-target",
        workdir="/workspace",
        journal_path="/journals/target.jsonl",
        registered_at="2026-07-30T10:00:00",
        completed_at="2026-07-30T10:05:00",
        review_fragment_id="codex:thread-1:turn-target",
    )

    fragment = CodexPendingFragment(
        "codex:thread-1:turn-target",
        (target,),
    )

    class HistoryRepo:
        def list_extracted_before(
            self,
            _fragment: CodexPendingFragment,
        ) -> tuple[CodexTurnRecord, ...]:
            return (history,)

    source = build_codex_review_source(
        cast(CodexTurnsRepository, HistoryRepo()),
        fragment,
    )

    assert source == ReviewSource(
        host_kind="codex",
        context=(JournalSlice("/journals/history.jsonl"),),
        target=(JournalSlice("/journals/target.jsonl"),),
    )


def test_rendered_source_keeps_context_before_target_and_states_roles() -> None:
    rendered = render_review_source(
        ReviewSource(
            host_kind="codex",
            context=(JournalSlice("/journals/history.jsonl"),),
            target=(JournalSlice("/journals/target.jsonl"),),
        )
    )

    assert "Codex" in rendered
    assert "列表顺序就是 turn 完成顺序" in rendered
    assert "历史上下文（按需查看，只用于理解，不属于本次处理目标）" in rendered
    assert "本次处理目标（必须全部读取）" in rendered
    assert rendered.index("/journals/history.jsonl") < rendered.index(
        "/journals/target.jsonl"
    )
