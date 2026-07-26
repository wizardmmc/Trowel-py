import sqlite3

from trowel_py.memory.sessions_repo import SessionRecord, create_sessions_repository


def test_incremental_queries_whitelist_memory_eligibility() -> None:
    repo = create_sessions_repository(sqlite3.connect(":memory:"))
    repo.register(
        SessionRecord(
            cc_session_id="eligible",
            workdir="/w",
            date="2026-07-26",
            registered_at="2026-07-26T00:00:00",
            memory_eligibility="eligible",
        )
    )
    repo.register(
        SessionRecord(
            cc_session_id="default",
            workdir="/isolated",
            date="2026-07-26",
            registered_at="2026-07-26T00:00:01",
            session_kind="default",
            memory_eligibility="ineligible",
        )
    )
    repo.update_completed("eligible", 10)
    repo.update_completed("default", 10)

    assert [item.session.cc_session_id for item in repo.find_incremental()] == [
        "eligible"
    ]
    assert [item.cc_session_id for item in repo.find_all_completed_sessions()] == [
        "eligible"
    ]


def test_codex_incremental_query_whitelists_memory_eligibility() -> None:
    repo = create_sessions_repository(sqlite3.connect(":memory:"))
    for suffix, eligibility in (("user", "eligible"), ("default", "ineligible")):
        repo.register_codex_turn(
            thread_id=f"thread-{suffix}",
            turn_id=f"turn-{suffix}",
            trowel_session_id=f"trowel-{suffix}",
            workdir="/w",
            journal_path=f"/{suffix}.jsonl",
            registered_at="2026-07-26T00:00:00",
            model="model",
            effort="high",
            provider="codex",
            memory_enabled=False,
            profile_enabled=False,
            session_kind=suffix,
            memory_eligibility=eligibility,
        )
        repo.complete_codex_turn(
            f"thread-{suffix}",
            f"turn-{suffix}",
            status="completed",
            completed_at="2026-07-26T00:01:00",
        )

    assert [item.turn.thread_id for item in repo.find_incremental_codex()] == [
        "thread-user"
    ]
