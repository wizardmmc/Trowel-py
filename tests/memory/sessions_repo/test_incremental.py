"""完成水位与增量区间。"""

from .support import repository, session_record


def test_update_completed_stamps_offset() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.update_completed("a", 2048, when="2026-07-11T02:30:00")
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT last_completed_offset, last_completed_at"
        " FROM sessions WHERE cc_session_id='a'"
    ).fetchone()
    assert row["last_completed_offset"] == 2048
    assert row["last_completed_at"] == "2026-07-11T02:30:00"


def test_find_incremental_returns_segment() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.update_completed("a", 2048, when="t")
    segments = repo.find_incremental()
    assert len(segments) == 1
    assert segments[0].session.cc_session_id == "a"
    assert segments[0].start == 0
    assert segments[0].end == 2048


def test_find_incremental_excludes_equal_offsets() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.update_completed("a", 2048, when="t")
    repo.advance_extracted("a", 2048, when="t2")
    assert repo.find_incremental() == []


def test_find_incremental_excludes_review() -> None:
    repo = repository()
    repo.register(
        session_record(
            cc_session_id="rev",
            session_kind="review",
        )
    )
    repo.update_completed("rev", 2048, when="t")
    assert repo.find_incremental() == []


def test_find_incremental_excludes_distill_and_eval_kinds() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="user", session_kind="user"))
    repo.register(
        session_record(
            cc_session_id="dist",
            session_kind="distill",
        )
    )
    repo.register(session_record(cc_session_id="ev", session_kind="eval"))
    repo.update_completed("user", 2048, when="t")
    repo.update_completed("dist", 2048, when="t")
    repo.update_completed("ev", 2048, when="t")
    segments = repo.find_incremental()
    assert [segment.session.cc_session_id for segment in segments] == ["user"]


def test_find_incremental_excludes_half_turn() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    assert repo.find_incremental() == []


def test_advance_extracted_stamps() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.advance_extracted("a", 4096, when="2026-07-11T02:35:00")
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT last_extracted_offset, last_extracted_at"
        " FROM sessions WHERE cc_session_id='a'"
    ).fetchone()
    assert row["last_extracted_offset"] == 4096
    assert row["last_extracted_at"] == "2026-07-11T02:35:00"
