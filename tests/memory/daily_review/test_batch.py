from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.memory.daily_review.support import (
    ERROR,
    FINISHED,
    VALID_DRAFT,
    FakeHost,
    factory,
    session,
)
from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.review_job import run_daily_review
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext


def _context_for_existing_episode() -> PersistContext:
    return PersistContext(
        segment_id="s1:old",
        cc_session_id="s1",
        workdir="/proj1",
        registered_at="2026-07-09T09:00:00",
        review_date="2026-07-09",
        source_jsonl="/old/source.jsonl",
        activity_dates=("2026-07-09",),
    )


def _existing_diary() -> DraftDiary:
    return DraftDiary(date="2026-07-09", outcomes=("已有 live episode",))


async def test_run_daily_review_persists_and_advances_all_segments(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("s1", "/proj1"))
    repo.claude.register(session("s2", "/proj2"))
    repo.claude.update_completed("s1", 4096)
    repo.claude.update_completed("s2", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    assert len(MemoryStore(memory_root).load_notes()) == 2
    conn = open_sessions_db(memory_root)
    assert create_sessions_repository(conn).claude.list_pending_segments() == []
    conn.close()


async def test_run_daily_review_keeps_failed_session_retryable(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("good", "/proj1"))
    repo.claude.register(session("bad", "/proj2"))
    repo.claude.update_completed("good", 4096)
    repo.claude.update_completed("bad", 4096)
    conn.close()

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        if session_record.native_session_id == "good":
            (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
            return FakeHost([FINISHED])
        return FakeHost([ERROR])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )

    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).claude.list_pending_segments()
    conn.close()
    assert [item.session.cc_session_id for item in pending] == ["bad"]
    assert len(MemoryStore(memory_root).load_notes()) == 1


async def test_immediate_review_only_processes_closed_cc_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(
        replace(session("closed", "/closed"), trowel_session_id="agent-closed")
    )
    repo.claude.register(replace(session("open", "/open"), trowel_session_id="agent-open"))
    repo.claude.update_completed("closed", 4096)
    repo.claude.update_completed("open", 4096)
    repo.review_requests.enqueue(
        "agent-closed",
        runtime="claude_code",
        requested_at="2026-07-09T12:00:00",
    )
    conn.close()
    calls: list[str] = []
    rebuilt_dates: list[str] = []

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session_record.native_session_id)
        (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._compress_or_aggregate",
        lambda _root, day, _provider: rebuilt_dates.append(day),
    )
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._maintain_dictionary",
        lambda _root, _provider: None,
    )

    await run_daily_review(
        event={"review_session_id": "agent-closed"},
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )

    assert calls == ["closed", "closed"]
    assert rebuilt_dates == ["2026-07-09"]
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        assert [item.session.cc_session_id for item in repo.claude.list_pending_segments()] == [
            "open"
        ]
        assert repo.review_requests.find("agent-closed") is None
    finally:
        conn.close()


async def test_each_closed_session_rebuilds_daily_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    for native_id, trowel_id in (("a", "agent-a"), ("b", "agent-b")):
        repo.claude.register(
            replace(
                session(native_id, f"/{native_id}"),
                trowel_session_id=trowel_id,
            )
        )
        repo.claude.update_completed(native_id, 4096)
        repo.review_requests.enqueue(
            trowel_id,
            runtime="claude_code",
            requested_at="2026-07-09T12:00:00",
        )
    conn.close()
    rebuilt_dates: list[str] = []
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._compress_or_aggregate",
        lambda _root, day, _provider: rebuilt_dates.append(day),
    )
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._maintain_dictionary",
        lambda _root, _provider: None,
    )

    for trowel_id in ("agent-a", "agent-b"):
        await run_daily_review(
            event={"review_session_id": trowel_id},
            memory_root=memory_root,
            date_str="2026-07-09",
            host_factory=factory([FINISHED], VALID_DRAFT),
        )

    assert rebuilt_dates == ["2026-07-09", "2026-07-09"]


async def test_cc_close_snapshot_does_not_absorb_later_resumed_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(
        replace(
            session("shared", "/shared"),
            trowel_session_id="agent-a",
        )
    )
    repo.claude.update_completed("shared", 2048)
    repo.review_requests.enqueue(
        "agent-a",
        runtime="claude_code",
        requested_at="2026-07-09T11:00:00",
    )
    repo.claude.register(
        replace(
            session("shared", "/shared"),
            trowel_session_id="agent-b",
            registered_at="2026-07-09T11:01:00",
        )
    )
    repo.claude.update_completed("shared", 4096)
    conn.close()
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._maintain_dictionary",
        lambda _root, _provider: None,
    )

    await run_daily_review(
        event={"review_session_id": "agent-a"},
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    manifests = sorted((memory_root / "meta" / "persisted-segments").glob("*.json"))
    assert [path.name for path in manifests] == ["shared:0:2048.json"]
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        [remaining] = repo.claude.list_pending_segments()
        assert (remaining.start, remaining.end) == (2048, 4096)
        assert repo.review_requests.find("agent-a") is None
    finally:
        conn.close()


async def test_cc_later_close_waits_for_unprocessed_earlier_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(
        replace(
            session("shared", "/shared"),
            trowel_session_id="agent-a",
        )
    )
    repo.claude.update_completed("shared", 2048)
    repo.claude.register(
        replace(
            session("shared", "/shared"),
            trowel_session_id="agent-b",
            registered_at="2026-07-09T11:01:00",
        )
    )
    repo.claude.update_completed("shared", 4096)
    repo.review_requests.enqueue(
        "agent-b",
        runtime="claude_code",
        requested_at="2026-07-09T11:02:00",
    )
    conn.close()
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._maintain_dictionary",
        lambda _root, _provider: None,
    )
    calls: list[str] = []

    def unexpected_factory(session_record: SessionRecord, _workdir: Path):
        calls.append(session_record.native_session_id)
        return factory([FINISHED], VALID_DRAFT)(session_record, _workdir)

    await run_daily_review(
        event={"review_session_id": "agent-b"},
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=unexpected_factory,
    )

    assert calls == []
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        assert repo.review_requests.find("agent-b") is not None
        [remaining] = repo.claude.list_pending_segments()
        assert (remaining.start, remaining.end) == (0, 4096)
    finally:
        conn.close()


async def test_failed_immediate_review_stays_queued_for_daily_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(replace(session("retry", "/retry"), trowel_session_id="agent-retry"))
    repo.claude.update_completed("retry", 4096)
    repo.review_requests.enqueue(
        "agent-retry",
        runtime="claude_code",
        requested_at="2026-07-09T12:00:00",
    )
    conn.close()
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._maintain_dictionary",
        lambda _root, _provider: None,
    )

    await run_daily_review(
        event={"review_session_id": "agent-retry"},
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([ERROR]),
    )

    conn = open_sessions_db(memory_root)
    try:
        assert (
            create_sessions_repository(conn).review_requests.find("agent-retry")
            is not None
        )
    finally:
        conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        assert repo.review_requests.find("agent-retry") is None
        assert repo.claude.list_pending_segments() == []
    finally:
        conn.close()


async def test_review_kind_session_never_enters_batch(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("user", "/project"))
    repo.claude.register(
        SessionRecord(
            cc_session_id="review-self",
            workdir="/runtime/review-daily-work/2026-07-09",
            date="2026-07-09",
            jsonl_path=session().jsonl_path,
            registered_at="2026-07-09T11:00:00",
            session_kind="review",
        )
    )
    repo.claude.update_completed("user", 4096)
    repo.claude.update_completed("review-self", 4096)
    conn.close()

    calls: list[str] = []

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session_record.native_session_id)
        (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )

    # 用户 session 会依次被 refine 与 judge 使用，review session 始终被排除。
    assert calls == ["user", "user"]


async def test_delegate_session_never_enters_daily_review(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("user", "/project"))
    repo.claude.register(
        replace(
            session("delegate", "/project"),
            session_kind="delegate",
        )
    )
    repo.claude.update_completed("user", 4096)
    repo.claude.update_completed("delegate", 4096)
    conn.close()

    calls: list[str] = []

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session_record.native_session_id)
        (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )

    assert calls == ["user", "user"]


async def test_daily_review_keeps_all_session_episodes(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    for session_id in ("s1", "s2", "s3"):
        repo.claude.register(session(session_id, f"/{session_id}"))
        repo.claude.update_completed(session_id, 4096)
    conn.close()

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        draft = json.dumps(
            {
                "notes": [
                    {
                        "title": f"结论 {session_record.native_session_id}",
                        "verification": "verified",
                    }
                ],
                "diary": [
                    {
                        "date": "2026-07-09",
                        "items": [
                            {
                                "kind": "outcome",
                                "summary": f"锚点 {session_record.native_session_id}",
                                "detail": "",
                            }
                        ],
                    }
                ],
            }
        )
        (workdir / "draft.json").write_text(draft, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )

    [daily] = MemoryStore(memory_root).load_diary(layer="day")
    assert "压缩版日记" in daily.body
    assert (memory_root / "episodes" / "s1.md").exists()
    assert (memory_root / "episodes" / "s2.md").exists()
    assert (memory_root / "episodes" / "s3.md").exists()


async def test_daily_review_writes_one_episode_per_session(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("s1", "/proj1"))
    repo.claude.register(session("s2", "/proj2"))
    repo.claude.update_completed("s1", 4096)
    repo.claude.update_completed("s2", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    episodes = sorted((memory_root / "episodes").glob("*.md"))
    assert [path.stem for path in episodes] == ["s1", "s2"]


async def test_persist_failure_does_not_advance_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("s1", "/proj1"))
    repo.claude.update_completed("s1", 4096)
    conn.close()

    def fail_episode_write(
        self: MemoryStore,
        context: object,
        diary_entries: object,
    ) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(MemoryStore, "write_episode", fail_episode_write)

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).claude.list_pending_segments()
    conn.close()
    assert [item.session.cc_session_id for item in pending] == ["s1"]
    assert not list((memory_root / "meta" / "persisted-segments").glob("*.json"))


async def test_schema_error_does_not_abort_or_advance_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("s1", "/proj1"))
    repo.claude.update_completed("s1", 4096)
    conn.close()

    def reject_schema(*_args: object, **_kwargs: object) -> None:
        raise ValueError("invalid note: kind=feedback")

    monkeypatch.setattr(
        "trowel_py.memory.daily_review.processor.persist_draft",
        reject_schema,
    )

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).claude.list_pending_segments()
    conn.close()
    assert [item.session.cc_session_id for item in pending] == ["s1"]


async def test_legacy_source_refs_in_new_draft_do_not_replace_live_episode(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    store = MemoryStore(memory_root)
    store.write_episode(
        _context_for_existing_episode(),
        (_existing_diary(),),
    )
    episode_path = memory_root / "episodes" / "s1.md"
    before = episode_path.read_bytes()

    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("s1", "/proj1"))
    repo.claude.update_completed("s1", 4096)
    conn.close()
    invalid = json.dumps(
        {
            "diary": [
                {
                    "date": "2026-07-09",
                    "items": [
                        {
                            "kind": "outcome",
                            "summary": "不应落盘",
                            "detail": "",
                            "source_refs": ["L999999"],
                        }
                    ],
                }
            ]
        }
    )

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], invalid),
    )

    assert episode_path.read_bytes() == before
    conn = open_sessions_db(memory_root)
    try:
        assert [
            item.session.cc_session_id
            for item in create_sessions_repository(conn).claude.list_pending_segments()
        ] == ["s1"]
    finally:
        conn.close()


async def test_rerun_after_failure_lands_each_artifact_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.claude.register(session("s1", "/proj1"))
    repo.claude.update_completed("s1", 4096)
    conn.close()

    calls = 0
    original_write_episode = MemoryStore.write_episode

    def flaky_write_episode(self, context, diary_entries):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient")
        return original_write_episode(self, context, diary_entries)

    monkeypatch.setattr(MemoryStore, "write_episode", flaky_write_episode)

    for _attempt in range(2):
        await run_daily_review(
            memory_root=memory_root,
            date_str="2026-07-09",
            host_factory=factory([FINISHED], VALID_DRAFT),
        )

    assert len(MemoryStore(memory_root).load_notes()) == 1
    assert (memory_root / "episodes" / "s1.md").exists()
    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).claude.list_pending_segments()
    conn.close()
    assert pending == []
