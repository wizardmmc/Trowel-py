"""tests for the profile-distill job orchestration (slice-050).

The cc host is injected via host_factory so no real cc is spawned (#46416).
Events are duck-typed (type=="finished" / type=="error"), matching
run_one_session's check. memory_root is tmp_path/"memory" so the sibling
distill-work dir lands inside tmp_path (test isolation).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trowel_py.memory.profile_distill_job import (
    DistillError,
    run_daily_distill,
    run_one_session,
)
from trowel_py.memory.profile_distill_state import (
    load_processed,
    mark_processed,
)
from trowel_py.memory.profile_suggestions import load_suggestions
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)

FINISHED = SimpleNamespace(type="finished")
ERROR = SimpleNamespace(type="error")

_VALID_DRAFT = json.dumps(
    {
        "suggestions": [
            {
                "dimension": "ability",
                "body": "网安硕士 / 红队背景",
                "sources": ["用户提到红队实习"],
                "rationale": "自述技术背景",
            }
        ]
    }
)


class FakeHost:
    """Yields preset events; the factory pre-places the draft in the workdir."""

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
            (workdir / "suggestions-draft.json").write_text(
                draft_text, encoding="utf-8"
            )
        return FakeHost(events)

    return factory


def _session(sid: str = "s1") -> SessionRecord:
    return SessionRecord(
        cc_session_id=sid,
        workdir="/proj",
        date="2026-07-14",
        jsonl_path="/x.jsonl",
        registered_at="2026-07-14T10:00:00",
    )


def _seed_session(root: Path, sid: str = "s1", completed: int = 1000) -> None:
    """Register a session + stamp a completed offset so it surfaces as a candidate."""
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.register(_session(sid))
        repo.update_completed(sid, completed)
    finally:
        conn.close()


# ---------- run_one_session ----------


async def test_run_one_session_parses_draft(tmp_path: Path) -> None:
    suggestions = await run_one_session(
        _session(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://127.0.0.1:8000",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.dimension == "ability"
    assert s.body == "网安硕士 / 红队背景"
    assert s.status == "pending"
    assert s.date == "2026-07-14"
    assert s.id  # job stamped a uuid


async def test_run_one_session_stamps_cc_session_in_sources(tmp_path: Path) -> None:
    # C-2: the agent's sources get cc_session_id prepended for traceability
    suggestions = await run_one_session(
        _session(sid="cc-sess-xyz"),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://x",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert "cc-sess-xyz" in suggestions[0].sources
    assert "用户提到红队实习" in suggestions[0].sources


async def test_run_one_session_empty_draft_ok(tmp_path: Path) -> None:
    # a session with no profile signal → empty draft, no raise (honest, not padded)
    empty = json.dumps({"suggestions": []})
    suggestions = await run_one_session(
        _session(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://x",
        host_factory=_factory([FINISHED], empty),
    )
    assert suggestions == []


async def test_run_one_session_no_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            _session(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=_factory([FINISHED], draft_text=None),
        )


async def test_run_one_session_not_finished_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            _session(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=_factory([ERROR], _VALID_DRAFT),
        )


async def test_run_one_session_bad_dimension_raises(tmp_path: Path) -> None:
    bad = json.dumps({"suggestions": [{"dimension": "personality", "body": "x"}]})
    with pytest.raises(DistillError):
        await run_one_session(
            _session(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=_factory([FINISHED], bad),
        )


# ---------- CCHost construction (C-4 proxy + C-5 kind) ----------


async def test_cchost_built_with_proxy_distill_kind_and_settings_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real CCHost path (host_factory=None) builds with proxy_base_url +
    session_kind='distill' (C-4 / C-5) AND settings_path (code-review [1] —
    the proxy strips provider vars, so settings_path must re-inject them or
    cc 401s). monkeypatch CCHost so no real cc spawns."""
    captured: dict = {}

    class FakeCCHost:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self._workdir = str(kwargs.get("workdir"))

        async def send(self, prompt: str):
            Path(self._workdir, "suggestions-draft.json").write_text(
                _VALID_DRAFT, encoding="utf-8"
            )
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr("trowel_py.cc_host.service.CCHost", FakeCCHost)
    await run_one_session(
        _session(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://127.0.0.1:8000",
        settings_path="/home/u/.claude/settings.json",
    )
    assert captured["proxy_base_url"] == "http://127.0.0.1:8000"
    assert captured["session_kind"] == "distill"
    assert captured["settings_path"] == "/home/u/.claude/settings.json"


# ---------- run_daily_distill ----------


async def test_run_daily_distill_appends_and_marks(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _seed_session(root, "s1", completed=1000)
    await run_daily_distill(
        root,
        "http://x",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
        date_str="2026-07-15",
    )
    pending = load_suggestions(root)
    assert len(pending) == 1
    assert pending[0].body == "网安硕士 / 红队背景"
    processed = load_processed(root)
    assert "s1" in processed
    assert processed["s1"].end_offset == 1000


async def test_run_daily_distill_failed_session_not_marked(tmp_path: Path) -> None:
    # C-6: an errored session is skipped WITHOUT marking → retried next run
    root = tmp_path / "memory"
    _seed_session(root, "s1", completed=1000)
    await run_daily_distill(
        root,
        "http://x",
        host_factory=_factory([ERROR], draft_text=None),
        date_str="2026-07-15",
    )
    assert load_suggestions(root) == []
    assert load_processed(root) == {}  # NOT marked → next run retries


async def test_run_daily_distill_skips_already_processed(tmp_path: Path) -> None:
    # completed == processed.end_offset → no new content → not distilled again
    root = tmp_path / "memory"
    _seed_session(root, "s1", completed=1000)
    mark_processed(root, "s1", end_offset=1000, at="2026-07-14T02:50:00")

    calls: list[str] = []

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session.cc_session_id)
        return FakeHost([FINISHED])

    await run_daily_distill(
        root, "http://x", host_factory=factory, date_str="2026-07-15"
    )
    assert calls == []
    assert load_suggestions(root) == []


async def test_run_daily_distill_redistills_new_offset(tmp_path: Path) -> None:
    # a resumed session whose completed offset grew past the watermark is re-distilled
    root = tmp_path / "memory"
    _seed_session(root, "s1", completed=2000)
    mark_processed(root, "s1", end_offset=1000, at="2026-07-14T02:50:00")

    await run_daily_distill(
        root,
        "http://x",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
        date_str="2026-07-15",
    )
    assert len(load_suggestions(root)) == 1
    assert load_processed(root)["s1"].end_offset == 2000


async def test_run_daily_distill_excludes_review_and_distill_kinds(
    tmp_path: Path,
) -> None:
    # C-5: the distill agent's own run (kind=distill) + review runs never enter the queue
    root = tmp_path / "memory"
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.register(_session("user"))
        repo.register(_session("rev"))
        repo.register(_session("dist"))
        repo.update_completed("user", 1000)
        repo.update_completed("rev", 1000)
        repo.update_completed("dist", 1000)
        # flip the kinds (register stamps kind on first write via SessionRecord)
        conn.execute("UPDATE sessions SET session_kind='review' WHERE cc_session_id='rev'")
        conn.execute(
            "UPDATE sessions SET session_kind='distill' WHERE cc_session_id='dist'"
        )
        conn.commit()
    finally:
        conn.close()

    calls: list[str] = []

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session.cc_session_id)
        (workdir / "suggestions-draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return FakeHost([FINISHED])

    await run_daily_distill(
        root, "http://x", host_factory=factory, date_str="2026-07-15"
    )
    assert calls == ["user"]
