from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

from tests.memory.review_job.support import (
    FINISHED,
    VALID_DRAFT,
    factory,
    session,
    write_jsonl,
)
from trowel_py.memory.review_job import _review_lock, run_daily_review
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore


async def test_review_creates_reusable_lock_file(tmp_path: Path) -> None:
    if fcntl is None:
        pytest.skip("当前平台不支持 flock")
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )
    assert (memory_root / "meta" / ".review.lock").exists()


def test_review_lock_is_mutually_exclusive(tmp_path: Path) -> None:
    if fcntl is None:
        pytest.skip("当前平台不支持 flock")
    memory_root = tmp_path / "memory"
    lock_path = memory_root / "meta" / ".review.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with pytest.raises(BlockingIOError):
            with _review_lock(memory_root):
                pass
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)


async def test_each_distilled_session_is_judged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    judged: list[str] = []

    async def fake_judge(
        session_record,
        review_date,
        root,
        *,
        host_factory=None,
        segment_id="",
    ) -> None:
        judged.append(session_record.cc_session_id)

    monkeypatch.setattr(
        "trowel_py.memory._review_batch.judge_session",
        fake_judge,
    )
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("s1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )
    assert judged == ["s1"]


async def test_judge_failure_does_not_roll_back_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fail_judge(*args, **kwargs) -> None:
        raise RuntimeError("judge blew up")

    monkeypatch.setattr(
        "trowel_py.memory._review_batch.judge_session",
        fail_judge,
    )
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("s1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    conn = open_sessions_db(memory_root)
    assert create_sessions_repository(conn).find_incremental() == []
    conn.close()
    assert len(MemoryStore(memory_root).load_notes()) == 1


def _seed_segment(memory_root: Path, session_id: str, jsonl_path: Path) -> int:
    size = write_jsonl(jsonl_path, ["2026-07-09T02:00:00.000Z"])
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(
        SessionRecord(
            cc_session_id=session_id,
            workdir="/project",
            date="2026-07-09",
            jsonl_path=str(jsonl_path),
            registered_at="2026-07-09T10:00:00",
        )
    )
    repo.update_completed(session_id, size)
    conn.close()
    return size


async def test_out_of_range_diary_date_keeps_segment_retryable(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    _seed_segment(memory_root, "s1", tmp_path / "s1.jsonl")
    draft = json.dumps(
        {
            "notes": [],
            "diary": [{"date": "2026-07-10", "outcomes": ["完成了 X"]}],
        }
    )

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], draft),
    )

    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).find_incremental()
    conn.close()
    assert len(pending) == 1
    assert not (memory_root / "episodes" / "s1.md").exists()


async def test_in_range_diary_date_lands_and_advances(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    _seed_segment(memory_root, "s1", tmp_path / "s1.jsonl")
    draft = json.dumps(
        {
            "notes": [],
            "diary": [{"date": "2026-07-09", "outcomes": ["完成事件提炼"]}],
        }
    )

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], draft),
    )

    conn = open_sessions_db(memory_root)
    pending = create_sessions_repository(conn).find_incremental()
    conn.close()
    assert pending == []
    assert (memory_root / "episodes" / "s1.md").exists()
