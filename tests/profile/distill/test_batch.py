from __future__ import annotations

import json
from pathlib import Path

from tests.profile.distill.support import (
    ERROR,
    FINISHED,
    VALID_DRAFT,
    FakeHost,
    draft_item,
    fake_host_factory,
    seed_codex_turn,
    seed_session,
    session_record,
)
from trowel_py.profile.distill.state import (
    load_codex_processed,
    load_processed,
    mark_processed,
)
from trowel_py.profile.distill import run_daily_distill
from trowel_py.profile.suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    load_suggestions,
)
from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)


async def test_run_daily_distill_appends_and_marks(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
        date_str="2026-07-15",
    )
    pending = load_suggestions(root)
    assert len(pending) == 1
    assert pending[0].body == "熟悉缓存一致性 / 并发调试"
    processed = load_processed(root)
    assert "s1" in processed
    assert processed["s1"].end_offset == 1000


async def test_run_daily_distill_failed_session_not_marked(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([ERROR], draft_text=None),
        date_str="2026-07-15",
    )
    assert load_suggestions(root) == []
    assert load_processed(root) == {}


async def test_run_daily_distill_skips_already_processed(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    mark_processed(root, "s1", end_offset=1000, at="2026-07-14T02:50:00")

    calls: list[str] = []

    def factory(source_id: str, workdir: Path) -> FakeHost:
        calls.append(source_id)
        return FakeHost([FINISHED])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )
    assert calls == []
    assert load_suggestions(root) == []


async def test_run_daily_distill_redistills_new_offset(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=2000)
    mark_processed(root, "s1", end_offset=1000, at="2026-07-14T02:50:00")

    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
        date_str="2026-07-15",
    )
    assert len(load_suggestions(root)) == 1
    assert load_processed(root)["s1"].end_offset == 2000


async def test_run_daily_distill_only_processes_user_sessions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.claude.register(session_record("user"))
        repo.claude.register(session_record("rev"))
        repo.claude.register(session_record("dist"))
        repo.claude.register(session_record("delegate"))
        repo.claude.update_completed("user", 1000)
        repo.claude.update_completed("rev", 1000)
        repo.claude.update_completed("dist", 1000)
        repo.claude.update_completed("delegate", 1000)
        conn.execute(
            "UPDATE sessions SET session_kind='review' WHERE cc_session_id='rev'"
        )
        conn.execute(
            "UPDATE sessions SET session_kind='distill' WHERE cc_session_id='dist'"
        )
        conn.execute(
            "UPDATE sessions SET session_kind='delegate' WHERE cc_session_id='delegate'"
        )
        conn.commit()
    finally:
        conn.close()

    calls: list[str] = []

    def factory(source_id: str, workdir: Path) -> FakeHost:
        calls.append(source_id)
        (workdir / "suggestions-draft.json").write_text(
            VALID_DRAFT,
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )
    assert calls == ["user"]


async def test_run_daily_distill_advances_when_all_gated_away(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    all_too_long = json.dumps({"suggestions": [draft_item(body="字" * 61)]})
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], all_too_long),
        date_str="2026-07-17",
    )
    assert load_suggestions(root) == []
    assert load_processed(root)["s1"].end_offset == 1000


async def test_run_daily_distill_bad_dim_does_not_advance(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    bad_dim = json.dumps({"suggestions": [draft_item(dim="personality")]})
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], bad_dim),
        date_str="2026-07-17",
    )
    assert load_processed(root) == {}
    assert load_suggestions(root) == []


async def test_run_daily_distill_dedup_ignores_v1_queue(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "v1-old",
                        "dimension": "methodology",
                        "body": "把 commit 写清楚这种很长的 v1 methodology 描述含例子",
                        "sources": ["old-cc"],
                        "date": "2026-07-01",
                        "status": "pending",
                    }
                ],
                "updated": "2026-07-01",
            }
        ),
        encoding="utf-8",
    )
    v2_draft = json.dumps(
        {"suggestions": [draft_item(body="commit 要让外行看懂", dim="methodology")]}
    )
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], v2_draft),
        date_str="2026-07-17",
    )
    loaded = load_suggestions(root)
    assert {suggestion.id for suggestion in loaded} == {"v1-old"} | {
        suggestion.id
        for suggestion in loaded
        if suggestion.policy_version == PROFILE_DISTILL_POLICY_VERSION
    }
    v2 = [
        suggestion
        for suggestion in loaded
        if suggestion.policy_version == PROFILE_DISTILL_POLICY_VERSION
    ]
    assert len(v2) == 1
    assert v2[0].body == "commit 要让外行看懂"


async def test_run_daily_distill_processes_codex_target_and_marks_turn(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    seed_codex_turn(root)
    draft = json.dumps(
        {
            "suggestions": [
                draft_item(
                    body="重视真实运行结果",
                    sources=["真实运行比只看单测更可信"],
                )
            ]
        },
        ensure_ascii=False,
    )

    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], draft),
        date_str="2026-07-15",
    )

    [suggestion] = load_suggestions(root)
    assert suggestion.body == "重视真实运行结果"
    assert suggestion.sources == (
        "codex:thread-1:turn-1",
        "真实运行比只看单测更可信",
    )
    assert ("thread-1", "turn-1") in load_codex_processed(root)


async def test_run_daily_distill_codex_eligibility_ignores_memory_and_injection_switch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    seed_codex_turn(
        root,
        profile_enabled=False,
        memory_extracted_at="2026-07-15T11:00:00",
    )
    calls: list[str] = []

    def factory(source_id: str, workdir: Path) -> FakeHost:
        calls.append(source_id)
        (workdir / "suggestions-draft.json").write_text(
            '{"suggestions":[]}',
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )

    assert calls == ["codex:thread-1:turn-1"]
    assert ("thread-1", "turn-1") in load_codex_processed(root)


async def test_run_daily_distill_merges_runtime_candidates_by_completion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    seed_session(root, "claude-late", completed=100)
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).claude.update_completed(
            "claude-late",
            100,
            when="2026-07-15T10:10:00",
        )
    finally:
        conn.close()
    seed_codex_turn(
        root,
        completed_at="2026-07-15T10:05:00",
        registered_at="2026-07-15T10:00:00",
    )
    calls: list[str] = []

    def factory(source_id: str, workdir: Path) -> FakeHost:
        calls.append(source_id)
        (workdir / "suggestions-draft.json").write_text(
            '{"suggestions":[]}',
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )

    assert calls == ["codex:thread-1:turn-1", "claude-late"]


async def test_codex_failure_blocks_later_turn_in_same_thread_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    seed_codex_turn(
        root,
        turn_id="turn-1",
        completed_at="2026-07-15T10:01:00",
    )
    seed_codex_turn(
        root,
        turn_id="turn-2",
        completed_at="2026-07-15T10:02:00",
        registered_at="2026-07-15T10:01:30",
    )
    seed_codex_turn(
        root,
        thread_id="thread-2",
        turn_id="turn-other",
        completed_at="2026-07-15T10:03:00",
        registered_at="2026-07-15T10:02:30",
    )
    calls: list[str] = []

    def factory(source_id: str, workdir: Path) -> FakeHost:
        calls.append(source_id)
        if source_id.endswith(":turn-1"):
            return FakeHost([ERROR])
        (workdir / "suggestions-draft.json").write_text(
            '{"suggestions":[]}',
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )

    assert calls == [
        "codex:thread-1:turn-1",
        "codex:thread-2:turn-other",
    ]
    processed = load_codex_processed(root)
    assert ("thread-1", "turn-1") not in processed
    assert ("thread-1", "turn-2") not in processed
    assert ("thread-2", "turn-other") in processed


async def test_codex_zero_suggestions_still_marks_turn(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_codex_turn(root)

    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], '{"suggestions":[]}'),
        date_str="2026-07-15",
    )

    assert load_suggestions(root) == []
    assert ("thread-1", "turn-1") in load_codex_processed(root)


async def test_codex_queue_failure_does_not_mark_turn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "memory"
    seed_codex_turn(root)
    draft = json.dumps(
        {"suggestions": [draft_item(sources=["真实运行比只看单测更可信"])]},
        ensure_ascii=False,
    )

    def fail_queue(*_args, **_kwargs) -> None:
        raise OSError("queue unavailable")

    monkeypatch.setattr(
        "trowel_py.profile.distill.batch.append_suggestions",
        fail_queue,
    )

    try:
        await run_daily_distill(
            root,
            "http://x",
            host_factory=fake_host_factory([FINISHED], draft),
            date_str="2026-07-15",
        )
    except OSError as exc:
        assert str(exc) == "queue unavailable"
    else:
        raise AssertionError("queue failure must propagate")

    assert load_codex_processed(root) == {}


async def test_codex_missing_target_journal_does_not_mark_turn(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    journal = seed_codex_turn(root)
    journal.unlink()

    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], '{"suggestions":[]}'),
        date_str="2026-07-15",
    )

    assert load_codex_processed(root) == {}


async def test_codex_state_failure_happens_after_queue_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "memory"
    seed_codex_turn(root)
    draft = json.dumps(
        {"suggestions": [draft_item(sources=["真实运行比只看单测更可信"])]},
        ensure_ascii=False,
    )

    def fail_state(*_args, **_kwargs) -> None:
        raise OSError("state unavailable")

    monkeypatch.setattr(
        "trowel_py.profile.distill.adapters.codex.mark_codex_processed",
        fail_state,
    )

    try:
        await run_daily_distill(
            root,
            "http://x",
            host_factory=fake_host_factory([FINISHED], draft),
            date_str="2026-07-15",
        )
    except OSError as exc:
        assert str(exc) == "state unavailable"
    else:
        raise AssertionError("state failure must propagate")

    assert len(load_suggestions(root)) == 1
    assert load_codex_processed(root) == {}


async def test_profile_sources_never_overlap_agent_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    seed_codex_turn(root, turn_id="turn-1")
    seed_codex_turn(
        root,
        turn_id="turn-2",
        completed_at="2026-07-15T10:06:00",
        registered_at="2026-07-15T10:05:30",
    )
    active = 0
    maximum = 0

    class ConcurrencyHost(FakeHost):
        async def send(self, prompt: str):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                yield FINISHED
            finally:
                active -= 1

    def factory(source_id: str, workdir: Path) -> FakeHost:
        (workdir / "suggestions-draft.json").write_text(
            '{"suggestions":[]}',
            encoding="utf-8",
        )
        return ConcurrencyHost([])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )

    assert maximum == 1
