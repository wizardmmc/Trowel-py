from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.review_job.support import (
    ERROR,
    FINISHED,
    VALID_DRAFT,
    FakeHost,
    factory,
    session,
)
from trowel_py.memory.review_job import run_daily_review
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore


async def test_run_daily_review_persists_and_advances_all_segments(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("s1", "/proj1"))
    repo.register(session("s2", "/proj2"))
    repo.update_completed("s1", 4096)
    repo.update_completed("s2", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    assert len(MemoryStore(memory_root).load_notes()) == 2
    conn = open_sessions_db(memory_root)
    assert create_sessions_repository(conn).find_incremental() == []
    conn.close()


async def test_run_daily_review_keeps_failed_session_retryable(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("good", "/proj1"))
    repo.register(session("bad", "/proj2"))
    repo.update_completed("good", 4096)
    repo.update_completed("bad", 4096)
    conn.close()

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        if session_record.cc_session_id == "good":
            (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
            return FakeHost([FINISHED])
        return FakeHost([ERROR])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )

    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).find_incremental()
    conn.close()
    assert [item.session.cc_session_id for item in pending] == ["bad"]
    assert len(MemoryStore(memory_root).load_notes()) == 1


async def test_review_kind_session_never_enters_batch(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("user", "/project"))
    repo.register(
        SessionRecord(
            cc_session_id="review-self",
            workdir="/runtime/review-daily-work/2026-07-09",
            date="2026-07-09",
            jsonl_path=session().jsonl_path,
            registered_at="2026-07-09T11:00:00",
            session_kind="review",
        )
    )
    repo.update_completed("user", 4096)
    repo.update_completed("review-self", 4096)
    conn.close()

    calls: list[str] = []

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session_record.cc_session_id)
        (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )

    # 用户 session 会依次被 refine 与 judge 使用，review session 始终被排除。
    assert calls == ["user", "user"]


async def test_daily_review_keeps_all_session_episodes(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    for session_id in ("s1", "s2", "s3"):
        repo.register(session(session_id, f"/{session_id}"))
        repo.update_completed(session_id, 4096)
    conn.close()

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        draft = json.dumps(
            {
                "notes": [
                    {
                        "title": f"结论 {session_record.cc_session_id}",
                        "verification": "verified",
                    }
                ],
                "diary": [
                    {
                        "date": "2026-07-09",
                        "outcomes": [f"锚点 {session_record.cc_session_id}"],
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
    repo.register(session("s1", "/proj1"))
    repo.register(session("s2", "/proj2"))
    repo.update_completed("s1", 4096)
    repo.update_completed("s2", 4096)
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
    repo.register(session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
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
    pending = create_sessions_repository(conn).find_incremental()
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
    repo.register(session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
    conn.close()

    def reject_schema(*_args: object, **_kwargs: object) -> None:
        raise ValueError("invalid note: kind=feedback")

    monkeypatch.setattr(
        "trowel_py.memory._review_batch.persist_draft",
        reject_schema,
    )

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).find_incremental()
    conn.close()
    assert [item.session.cc_session_id for item in pending] == ["s1"]


async def test_rerun_after_failure_lands_each_artifact_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
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
    pending = create_sessions_repository(conn).find_incremental()
    conn.close()
    assert pending == []
