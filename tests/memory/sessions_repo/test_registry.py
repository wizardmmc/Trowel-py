"""Session 注册、筛选和完成记录。"""

from .support import complete, repository, session_record


def test_register_and_find_pending() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.register(session_record(cc_session_id="b"))
    assert len(repo.find_pending("2026-07-09")) == 2


def test_find_pending_filters_by_date() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a", date="2026-07-09"))
    repo.register(session_record(cc_session_id="b", date="2026-07-10"))
    assert len(repo.find_pending("2026-07-09")) == 1


def test_mark_extracted_excludes_from_pending() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.mark_extracted("a", "2026-07-10T02:17:00")
    assert repo.find_pending("2026-07-09") == []


def test_find_pending_excludes_review_workdir() -> None:
    repo = repository()
    repo.register(
        session_record(
            cc_session_id="user",
            workdir="/workspace/project",
        )
    )
    repo.register(
        session_record(
            cc_session_id="review",
            workdir="/workspace/.trowel/review-daily-work/2026-07-09",
        )
    )
    pending = repo.find_pending(
        "2026-07-09",
        exclude_workdir_substr="review-daily-work",
    )
    assert len(pending) == 1
    assert pending[0].cc_session_id == "user"


def test_register_is_idempotent_on_session_id() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a", workdir="/project/first"))
    repo.register(session_record(cc_session_id="a", workdir="/project/second"))
    rows = repo.find_pending("2026-07-09")
    assert len(rows) == 1
    assert rows[0].workdir == "/project/first"


def test_find_all_completed_returns_sessions_with_completed_offset() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.register(session_record(cc_session_id="b"))
    repo.register(session_record(cc_session_id="c"))
    complete(repo, "a", "b")
    found = repo.find_all_completed_sessions()
    assert {session.cc_session_id for session in found} == {"a", "b"}


def test_find_all_completed_excludes_review_and_distill_kinds() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="user", session_kind="user"))
    repo.register(session_record(cc_session_id="rev", session_kind="review"))
    repo.register(
        session_record(
            cc_session_id="dist",
            session_kind="distill",
        )
    )
    complete(repo, "user", "rev", "dist")
    found = repo.find_all_completed_sessions()
    assert {session.cc_session_id for session in found} == {"user"}


def test_find_all_completed_ignores_review_extracted_state() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a"))
    repo.update_completed("a", 1000)
    repo.advance_extracted("a", 1000)
    found = repo.find_all_completed_sessions()
    assert {session.cc_session_id for session in found} == {"a"}


def test_find_all_completed_ordered_by_registered_at() -> None:
    repo = repository()
    repo.register(
        session_record(
            cc_session_id="late",
            registered_at="2026-07-10T10:00:00",
        )
    )
    repo.register(
        session_record(
            cc_session_id="early",
            registered_at="2026-07-09T10:00:00",
        )
    )
    complete(repo, "late", "early")
    found = repo.find_all_completed_sessions()
    assert [session.cc_session_id for session in found] == ["early", "late"]


def test_find_all_completed_custom_exclude_kinds() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="a", session_kind="user"))
    repo.register(session_record(cc_session_id="b", session_kind="custom"))
    complete(repo, "a", "b")
    found = repo.find_all_completed_sessions(exclude_kinds=["custom"])
    assert {session.cc_session_id for session in found} == {"a"}


def test_find_pending_preserves_jsonl_path() -> None:
    repo = repository()
    repo.register(
        session_record(
            cc_session_id="a",
            jsonl_path="/sessions/a.jsonl",
        )
    )
    [record] = repo.find_pending("2026-07-09")
    assert record.jsonl_path == "/sessions/a.jsonl"


def test_session_kind_defaults_user() -> None:
    assert session_record().session_kind == "user"


def test_register_writes_session_kind() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="r1", session_kind="review"))
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT session_kind FROM sessions WHERE cc_session_id = ?",
        ("r1",),
    ).fetchone()
    assert row["session_kind"] == "review"


def test_old_row_null_kind_backfills_user() -> None:
    repo = repository()
    repo._conn.execute(  # noqa: SLF001
        "INSERT INTO sessions"
        " (cc_session_id, workdir, date, registered_at, session_kind)"
        " VALUES ('legacy', '/workspace', '2026-07-09', 't', NULL)"
    )
    repo._conn.commit()  # noqa: SLF001
    [record] = repo.find_pending("2026-07-09")
    assert record.session_kind == "user"


def test_find_pending_exclude_kinds() -> None:
    repo = repository()
    repo.register(
        session_record(
            cc_session_id="user1",
            session_kind="user",
            workdir="/workspace/project",
        )
    )
    repo.register(
        session_record(
            cc_session_id="rev1",
            session_kind="review",
            workdir="/workspace/project",
        )
    )
    pending = repo.find_pending("2026-07-09", exclude_kinds=["review"])
    assert len(pending) == 1
    assert pending[0].cc_session_id == "user1"
