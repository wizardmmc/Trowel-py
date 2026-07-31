"""Claude Code 会话到 Profile 提炼候选和来源的适配测试。"""

from trowel_py.memory.sessions_repo import ClaudeSessionRecord
from trowel_py.profile.distill.adapters.claude import build_claude_backlog
from trowel_py.profile.distill.sources.claude import build_claude_distill_source
from trowel_py.profile.distill.state import ProcessedSession


def _session(session_id: str, completed: int | None) -> ClaudeSessionRecord:
    """构造带完成水位的最小 Claude Code 会话记录。"""
    return ClaudeSessionRecord(
        cc_session_id=session_id,
        workdir="/project",
        date="2026-07-31",
        jsonl_path=f"/transcripts/{session_id}.jsonl",
        registered_at="2026-07-31T10:00:00",
        last_completed_offset=completed,
    )


def test_backlog_keeps_order_and_only_includes_new_bytes() -> None:
    """候选顺序和增量区间应与迁移前的逐会话计算一致。"""
    sessions = [_session("new", 100), _session("same", 20), _session("more", 80)]
    processed = {
        "same": ProcessedSession("same", 20, "2026-07-30T02:50:00"),
        "more": ProcessedSession("more", 30, "2026-07-30T02:50:00"),
    }

    backlog = build_claude_backlog(sessions, processed)

    assert [
        (
            candidate.runtime,
            candidate.label,
            candidate.start_offset,
            candidate.end_offset,
        )
        for candidate in backlog
    ] == [
        ("claude_code", "new", 0, 100),
        ("claude_code", "more", 30, 80),
    ]


def test_backlog_treats_missing_completed_offset_as_zero() -> None:
    """尚无完成字节的会话不应进入提炼候选。"""
    assert build_claude_backlog([_session("unfinished", None)], {}) == []


def test_source_preserves_jsonl_path_and_offsets() -> None:
    """来源适配不得改写现有 prompt 使用的路径和字节边界。"""
    source = build_claude_distill_source(
        source_id="s1",
        jsonl_path="/transcripts/s1.jsonl",
        completed_at="2026-07-31T10:00:00",
        start_offset=10,
        end_offset=90,
    )

    assert source.source_id == "s1"
    assert source.jsonl_path == "/transcripts/s1.jsonl"
    assert source.start_offset == 10
    assert source.end_offset == 90
