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
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

try:  # Unix-only; the lock-mutex test skips off-Unix.
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.review_job import DistillError, run_daily_review, run_one_session
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore

FINISHED = SimpleNamespace(type="finished")
ERROR = SimpleNamespace(type="error")


@pytest.fixture(autouse=True)
def _stub_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """slice-041: stub AnthropicProvider so review_job tests never hit the
    network (daily compress + dictionary sync go through the fake). Tests that
    need a specific compressed body inject ``provider=`` explicitly."""
    class _FakeProvider:
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            return "压缩版日记"

    monkeypatch.setattr(
        "trowel_py.llm.client.AnthropicProvider",
        lambda _cfg: _FakeProvider(),
    )


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


async def test_review_job_uses_review_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The distillation CCHost is built with session_kind='review' (C-5).

    Without this stamp, the review session's own cc init would register a
    'user'-kind row and the next daily review would try to distill the
    distillation session → self-recursion. monkeypatch CCHost so no real cc is
    spawned (#46416) and capture the construction kwargs.
    """
    captured: dict = {}

    class FakeCCHost:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self._wd = str(kwargs["workdir"])

        async def send(self, prompt: str):
            Path(self._wd, "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr("trowel_py.cc_host.service.CCHost", FakeCCHost)

    await run_one_session(_session(), "2026-07-09", tmp_path / "memory")
    assert captured.get("session_kind") == "review"


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
    repo.update_completed("s1", 4096)
    repo.update_completed("s2", 4096)
    conn.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )

    assert len(MemoryStore(mem).load_notes()) == 2
    conn2 = open_sessions_db(mem)
    # both segments advanced → no incremental work left (slice-040-b C-7)
    assert create_sessions_repository(conn2).find_incremental() == []
    conn2.close()


async def test_run_daily_review_skips_failed_session(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("good", "/proj1"))
    repo.register(_session("bad", "/proj2"))
    repo.update_completed("good", 4096)
    repo.update_completed("bad", 4096)
    conn.close()

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        if session.cc_session_id == "good":
            (workdir / "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
            return FakeHost([FINISHED])
        return FakeHost([ERROR])  # bad session errors

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    # good advanced; bad NOT advanced (still incremental → retryable)
    conn2 = open_sessions_db(mem)
    pending = create_sessions_repository(conn2).find_incremental()
    conn2.close()
    assert [p.session.cc_session_id for p in pending] == ["bad"]
    assert len(MemoryStore(mem).load_notes()) == 1  # only good landed


async def test_review_workdir_session_not_processed(tmp_path: Path) -> None:
    # slice-040-b C-5: the distillation session itself is filtered out by
    # session_kind='review' (not by workdir-path guessing). Here the review-self
    # session has a completed water mark too, so the ONLY thing keeping it out
    # of the queue is its kind.
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
            session_kind="review",
        )
    )
    repo.update_completed("user", 4096)
    repo.update_completed("review-self", 4096)
    conn.close()

    calls: list[str] = []

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session.cc_session_id)
        (workdir / "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    # slice-053: the user session is now touched TWICE — once by the refine
    # agent (run_one_session) and once by the judge agent — but review-self is
    # still never touched (kept out by kind, C-5).
    assert calls == ["user", "user"]


# ---------- slice-040-a: P1 daily aggregate + atomic + idempotent rerun ----------


async def test_daily_review_daily_aggregates_all_sessions(tmp_path: Path) -> None:
    # P1 回归：3 个 session 同日 review → 派生 daily 含全部 3 个锚点
    # （覆盖 bug 只剩最后 1 个）。
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.register(_session("s2", "/proj2"))
    repo.register(_session("s3", "/proj3"))
    repo.update_completed("s1", 4096)
    repo.update_completed("s2", 4096)
    repo.update_completed("s3", 4096)
    conn.close()

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        draft = json.dumps(
            {
                "notes": [
                    {"title": f"结论 {session.cc_session_id}", "verification": "verified"}
                ],
                "diary": [
                    {"date": "2026-07-09", "events": f"锚点 {session.cc_session_id}"}
                ],
            }
        )
        (workdir / "draft.json").write_text(draft, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    # slice-041: daily is now the LLM-compressed body (stub returns a fixed
    # string). The P1 regression — 3 sessions not overwriting each other — is
    # verified at the episode layer (each session has its own file).
    [d] = MemoryStore(mem).load_diary(layer="day")
    assert d.body == "压缩版日记"
    assert (mem / "episodes" / "s1.md").exists()
    assert (mem / "episodes" / "s2.md").exists()
    assert (mem / "episodes" / "s3.md").exists()


async def test_daily_review_writes_per_session_episodes(tmp_path: Path) -> None:
    # P1：3 个 session → 3 个独立 episode 文件（不是 1 个被覆盖的 daily）。
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.register(_session("s2", "/proj2"))
    repo.update_completed("s1", 4096)
    repo.update_completed("s2", 4096)
    conn.close()

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        (workdir / "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    eps = sorted((mem / "episodes").glob("*.md"))
    assert [p.stem for p in eps] == ["s1", "s2"]


async def test_persist_failure_does_not_mark_extracted(
    tmp_path: Path, monkeypatch
) -> None:
    # C-7 原子水位：persist 中途失败 → session 不 mark_extracted（可重试）。
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
    conn.close()

    from trowel_py.memory.store import MemoryStore as _MS

    def boom(self, context, diary_entries):  # noqa: ANN001
        raise OSError("disk full")

    monkeypatch.setattr(_MS, "write_episode", boom)

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        (workdir / "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    # segment not advanced (extracted mark stays at 0) → retryable
    conn2 = open_sessions_db(mem)
    pending = create_sessions_repository(conn2).find_incremental()
    conn2.close()
    assert [p.session.cc_session_id for p in pending] == ["s1"]
    # and no manifest was written (the failure aborted before it)
    assert not list((mem / "meta" / "persisted-segments").glob("*.json"))


async def test_rerun_after_failure_lands_exactly_one(tmp_path: Path, monkeypatch) -> None:
    # C-7 重跑：第一次 persist 失败（episode 没写、manifest 没写、note 写了），
    # 第二次成功 → 每个产物恰好一份（note 不翻倍，靠幂等 + manifest）。
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
    conn.close()

    from trowel_py.memory.store import MemoryStore as _MS

    call_count = {"n": 0}
    orig_write_episode = _MS.write_episode

    def flaky(self, context, diary_entries):  # noqa: ANN001
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("transient")
        return orig_write_episode(self, context, diary_entries)

    monkeypatch.setattr(_MS, "write_episode", flaky)

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        (workdir / "draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    # first run: persist fails → segment not advanced
    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )
    # second run: succeeds → exactly one note, one episode, advanced
    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )

    assert len(MemoryStore(mem).load_notes()) == 1
    assert (mem / "episodes" / "s1.md").exists()
    conn2 = open_sessions_db(mem)
    pending = create_sessions_repository(conn2).find_incremental()
    conn2.close()
    assert pending == []  # advanced this time


# ---------- slice-040-b: incremental distillation (T12) --------------------


async def test_incremental_segment_id_carries_offsets(tmp_path: Path) -> None:
    """segment_id = <sid>:<start>:<end> → resumed slices get distinct manifests."""
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )

    manifests = sorted((mem / "meta" / "persisted-segments").glob("*.json"))
    assert [p.name for p in manifests] == ["s1:0:4096.json"]


async def test_resume_only_distills_new_slice(tmp_path: Path) -> None:
    """C-7: a resumed session's 2nd run distils only (last_extracted, completed]."""
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.update_completed("s1", 2048)
    conn.close()

    # first run: distil slice 0:2048
    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert (mem / "meta" / "persisted-segments" / "s1:0:2048.json").exists()

    # resume: session completes more turns → completed mark moves to 4096
    conn2 = open_sessions_db(mem)
    repo2 = create_sessions_repository(conn2)
    repo2.update_completed("s1", 4096)
    conn2.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )

    # both slices' manifests coexist (per-segment upsert in one episode file)
    seg_dir = mem / "meta" / "persisted-segments"
    assert (seg_dir / "s1:0:2048.json").exists()
    assert (seg_dir / "s1:2048:4096.json").exists()

    conn3 = open_sessions_db(mem)
    assert create_sessions_repository(conn3).find_incremental() == []
    conn3.close()


async def test_half_turn_not_distilled(tmp_path: Path) -> None:
    """C-6: no completed water mark (result not seen yet) → never distilled."""
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    # no update_completed — simulates a half-turn (user/tool_result only, no result)
    conn.close()

    calls: list[str] = []

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session.cc_session_id)
        return FakeHost([FINISHED])

    await run_daily_review(
        None, memory_root=mem, date_str="2026-07-09", host_factory=factory
    )
    assert calls == []  # the half-turn session never entered the queue


async def test_review_uses_date_str_not_session_date(tmp_path: Path) -> None:
    """review_date = date_str (not session.date) — cross-day sessions归 CLI date.

    A session registered with date=2026-07-08 (started that day, carried into
    07-09) must land in the 07-09 daily when the user runs ``review --date
    2026-07-09`` — matching 040-a behavior. Otherwise stray 07-08/07-10 dailies
    appear for sessions the user never explicitly reviewed that day.
    """
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(
        SessionRecord(
            cc_session_id="s1",
            workdir="/proj",
            date="2026-07-08",  # session started 07-08
            jsonl_path="",
            registered_at="2026-07-08T10:00:00",
        )
    )
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",  # review run for 07-09
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )

    ep = (mem / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "2026-07-09" in ep  # review_date = date_str, not session.date
    assert (mem / "diary" / "daily" / "2026-07-09.md").exists()
    assert not (mem / "diary" / "daily" / "2026-07-08.md").exists()  # no stray daily


async def test_refine_prompt_gets_incremental_range(tmp_path: Path) -> None:
    """The agent's prompt carries the incremental byte range (slice-040-b)."""
    from trowel_py.memory.prompt import build_refine_prompt

    whole = build_refine_prompt("/x.jsonl", "tokens=0")
    assert "增量范围" not in whole  # whole-session distill: no range header

    incr = build_refine_prompt(
        "/x.jsonl", "tokens=0", start_offset=2048, end_offset=4096
    )
    assert "增量范围" in incr
    assert "[2048, 4096]" in incr


# ---------- slice-040-b T15: review flock mutex (C-3) --------------------


async def test_review_creates_lock_file(tmp_path: Path) -> None:
    """run_daily_review holds an flock on meta/.review.lock for its duration."""
    if fcntl is None:
        pytest.skip("flock no-op path does not create the lock file off-Unix")
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    # the lock file is created (and left behind — flock released on close, the
    # file itself is not removed; that's fine, it's reused next run).
    assert (mem / "meta" / ".review.lock").exists()


def test_review_lock_is_mutually_exclusive(tmp_path: Path) -> None:
    """A second fd already holding the lock makes _review_lock raise."""
    if fcntl is None:
        pytest.skip("fcntl not available on this platform")
    from trowel_py.memory.review_job import _review_lock

    mem = tmp_path / "memory"
    lock = mem / "meta" / ".review.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    # hold the lock from a separate fd first (same process, different fd →
    # different file description → flock blocks, this is what we want to prove).
    holder = os.open(str(lock), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with pytest.raises(BlockingIOError):
            with _review_lock(mem):
                pass
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)


# ---------- slice-053: judge orchestration (C-2 isolation) ----------


async def test_run_daily_review_judges_each_distilled_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """slice-053: after a session lands + is advanced, judge_session runs on it
    (D4: distill → judge the same session in place). judge_session is mocked so
    no real judge agent spawns; we only assert the orchestration calls it."""
    judged: list[str] = []

    async def fake_judge(session, review_date, root, *, host_factory=None):
        judged.append(session.cc_session_id)
        return None

    monkeypatch.setattr("trowel_py.memory.review_job.judge_session", fake_judge)
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert judged == ["s1"]  # judged exactly the distilled session


async def test_judge_failure_does_not_block_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C-2: even if judge_session RAISES (breaches its own internal swallow),
    review's advance_extracted still completes and the note still lands."""
    async def raising_judge(*a, **kw):
        raise RuntimeError("judge blew up")

    monkeypatch.setattr("trowel_py.memory.review_job.judge_session", raising_judge)
    mem = tmp_path / "memory"
    conn = open_sessions_db(mem)
    repo = create_sessions_repository(conn)
    repo.register(_session("s1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        None,
        memory_root=mem,
        date_str="2026-07-09",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    # review still advanced → no incremental work left
    conn2 = open_sessions_db(mem)
    assert create_sessions_repository(conn2).find_incremental() == []
    conn2.close()
    assert len(MemoryStore(mem).load_notes()) == 1  # note still landed


