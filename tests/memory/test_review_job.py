"""tests for the review-job orchestration (slice-040 T11).

The cc host is injected via host_factory so no real cc is spawned (#46416).
Events are duck-typed (type=="finished" / type=="error") matching what
run_one_session checks.

memory_root is set to ``tmp_path/"memory"`` to mirror the real layout
(``~/.trowel/memory``), so the sibling review-daily-work dir lands inside
tmp_path too — not in the shared pytest tmp parent (test isolation).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trowel_py.memory.review_job import DistillError, run_daily_review, run_one_session
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore

FINISHED = SimpleNamespace(type="finished")
ERROR = SimpleNamespace(type="error")


class FakeHost:
    """Yields preset events; the factory pre-places draft.json in the workdir."""

    def __init__(self, events: list) -> None:
        self._events = events

    async def send(self, prompt: str):
        for ev in self._events:
            yield ev

    async def close(self) -> None:
        pass


def _factory(events: list, draft_text: str | None = None):
    """Build a host_factory that writes draft_text into the workdir before running."""

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        if draft_text is not None:
            (workdir / "draft.json").write_text(draft_text, encoding="utf-8")
        return FakeHost(events)

    return factory


def _session(sid: str = "s1", workdir: str = "/proj") -> SessionRecord:
    return SessionRecord(
        cc_session_id=sid,
        workdir=workdir,
        date="2026-07-09",
        jsonl_path="",
        registered_at="2026-07-09T10:00:00",
    )


_VALID_DRAFT = json.dumps(
    {
        "notes": [{"title": "结论", "verification": "verified"}],
        "diary": [{"date": "2026-07-09", "events": "事件流"}],
    }
)


async def test_run_one_session_reads_draft(tmp_path: Path) -> None:
    draft = await run_one_session(
        _session(),
        "2026-07-09",
        tmp_path / "memory",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert len(draft.notes) == 1
    assert draft.notes[0].verification == "verified"


async def test_run_one_session_error_raises(tmp_path: Path) -> None:
    # agent errored (no finished event) → DistillError, no draft read.
    with pytest.raises(DistillError):
        await run_one_session(
            _session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=_factory([ERROR], _VALID_DRAFT),
        )


async def test_run_one_session_no_draft_raises(tmp_path: Path) -> None:
    # finished but agent forgot to write draft.json → DistillError.
    with pytest.raises(DistillError):
        await run_one_session(
            _session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=_factory([FINISHED], draft_text=None),
        )


async def test_run_one_session_invalid_draft_raises(tmp_path: Path) -> None:
    bad = json.dumps({"notes": [{"title": "x", "verification": "bogus"}]})
    with pytest.raises(DistillError):
        await run_one_session(
            _session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=_factory([FINISHED], bad),
        )


async def test_run_one_session_malformed_draft_raises(tmp_path: Path) -> None:
    # W6: a malformed draft.json (bad JSON / non-int pain) must raise
    # DistillError — not a raw exception that would crash the whole daily review.
    with pytest.raises(DistillError):
        await run_one_session(
            _session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=_factory([FINISHED], "{not valid json"),
        )
    with pytest.raises(DistillError):
        await run_one_session(
            _session("s2"),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=_factory(
                [FINISHED], json.dumps({"notes": [{"title": "x", "pain": "high"}]})
            ),
        )


async def test_run_daily_review_persists_and_marks(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.register(_session("s2", "/proj2"))
    conn.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )

    assert len(MemoryStore(mem).load_notes()) == 2
    conn2 = open_sessions_db(mem)
    assert create_sessions_repository(conn2).find_pending("2026-07-09") == []
    conn2.close()


async def test_run_daily_review_skips_failed_session(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("good", "/proj1"))
    repo.register(_session("bad", "/proj2"))
    conn.close()

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        if session.cc_session_id == "good":
            (workdir / "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
            return FakeHost([FINISHED])
        return FakeHost([ERROR])  # bad session errors

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    # good persisted + marked; bad NOT marked (still pending → retryable)
    conn2 = open_sessions_db(mem)
    pending = create_sessions_repository(conn2).find_pending("2026-07-09")
    conn2.close()
    assert [p.cc_session_id for p in pending] == ["bad"]
    assert len(MemoryStore(mem).load_notes()) == 1  # only good landed


async def test_review_workdir_session_not_processed(tmp_path: Path) -> None:
    # D2: the distillation session itself (review-daily-work workdir) must be
    # filtered out by find_pending and never distilled.
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("user", "/Users/x/proj"))
    repo.register(
        SessionRecord(
            cc_session_id="review-self",
            workdir="/Users/x/.trowel/review-daily-work/2026-07-09",
            date="2026-07-09",
            jsonl_path="",
            registered_at="2026-07-09T11:00:00",
        )
    )
    conn.close()

    calls: list[str] = []

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session.cc_session_id)
        (workdir / "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    assert calls == ["user"]  # review-self never distilled
