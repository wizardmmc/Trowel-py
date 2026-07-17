"""tests for the judge_session orchestration (slice-053).

The cc host is injected via host_factory so no real cc is spawned (#46416 —
never nest claude -p in an interactive session). Events are duck-typed
(type=="finished" / type=="error"). The judged session's access-log is seeded
directly so the pre-extraction (C-3 filter by cc_session_id) is exercised.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trowel_py.memory.access_log import AccessRecord, log_access
from trowel_py.memory.judge import judge_session
from trowel_py.memory.judgements import (
    HitJudgement,
    JudgementReport,
    MissJudgement,
    load_judgement_report,
)
from trowel_py.memory.sessions_repo import SessionRecord

FINISHED = SimpleNamespace(type="finished")
ERROR = SimpleNamespace(type="error")

_VALID_DRAFT = json.dumps(
    {
        "hits": [
            {
                "memory_id": "real-note",
                "used": True,
                "outcome": "helpful",
                "reason": "模型引用了它",
                "evidence": "turn 3 改方向",
            }
        ],
        "recall_miss": [
            {
                "memory_id": "real-note-2",
                "attribution": "retrieval_miss",
                "reason": "当时没搜到",
                "evidence": "无相关 search",
            }
        ],
        "summary": "用得还行",
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
            (workdir / "judgement-draft.json").write_text(draft_text, encoding="utf-8")
        return FakeHost(events)

    return factory


def _session(sid: str = "judged-1") -> SessionRecord:
    return SessionRecord(
        cc_session_id=sid,
        workdir="/proj",
        date="2026-07-16",
        jsonl_path="/x.jsonl",
        registered_at="2026-07-16T10:00:00",
    )


def _seed_real_notes(root: Path, memory_ids: tuple[str, ...]) -> None:
    """Write minimal real notes carrying explicit memory_ids (C-6 known set).

    Written as raw md (not write_note) so the frontmatter carries ``type: note``
    + ``memory_id`` — the two fields ``_note_from_fm`` requires to surface a
    note (write_note omits ``type``). The draft's memory_ids reference these.
    """
    ndir = root / "notes"
    ndir.mkdir(parents=True, exist_ok=True)
    for mid in memory_ids:
        (ndir / f"{mid}.md").write_text(
            "---\n"
            "type: note\n"
            f"title: {mid}\n"
            "kind: fact\n"
            f"summary: {mid}\n"
            "verification: verified\n"
            f"memory_id: {mid}\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )


# ---------- parse + C-6 ----------


async def test_judge_session_parses_draft_and_saves(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _seed_real_notes(root, ("real-note", "real-note-2"))
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert report is not None
    assert isinstance(report, JudgementReport)
    assert report.cc_session_id == "judged-1"
    assert len(report.hits) == 1
    h = report.hits[0]
    assert isinstance(h, HitJudgement)
    assert h.used is True
    assert h.outcome == "helpful"
    assert h.reason  # C-4: reason present
    assert h.evidence  # C-4: evidence present
    assert len(report.recall_miss) == 1
    assert isinstance(report.recall_miss[0], MissJudgement)
    assert report.recall_miss[0].attribution == "retrieval_miss"
    # saved to disk (C-3 path)
    assert load_judgement_report(root, "judged-1") == report


async def test_judge_session_drops_fabricated_memory_ids(tmp_path: Path) -> None:
    # C-6: only real-note is a real note; real-note-2 and ghost are fabricated.
    root = tmp_path / "memory"
    _seed_real_notes(root, ("real-note",))
    draft = json.dumps(
        {
            "hits": [
                {"memory_id": "real-note", "used": True, "outcome": "helpful",
                 "reason": "r", "evidence": "e"},
                {"memory_id": "ghost", "used": False, "outcome": "unused",
                 "reason": "r", "evidence": "e"},
            ],
            "recall_miss": [
                {"memory_id": "ghost-2", "attribution": "awareness_miss",
                 "reason": "r", "evidence": "e"},
            ],
            "summary": "x",
        }
    )
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], draft),
    )
    assert report is not None
    assert {h.memory_id for h in report.hits} == {"real-note"}
    assert report.recall_miss == ()


# ---------- access-log pre-extraction (C-3) ----------


def _access(cc_session_id: str, action: str, memory_id: str = "", query: str = "") -> AccessRecord:
    return AccessRecord(
        ts="2026-07-16T10:00:00",
        trowel_session_id="t1",
        cc_session_id=cc_session_id,
        toolUseId="tu-1",
        action=action,  # type: ignore[arg-type]
        search_id="s1" if action == "search" else "",
        read_id="r1" if action == "read" else "",
        query=query,
        memory_id=memory_id,
        rank=1 if action == "search" else None,
    )


async def test_judge_prompt_only_sees_judged_session_access_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # C-3: the pre-extracted summary filters by the JUDGED session's cc_session_id.
    # Seed access-log for judged-1 AND a stray eval-kind session; only judged-1's
    # records must reach the prompt.
    root = tmp_path / "memory"
    log_access(root, _access("judged-1", "search", query="红队"))
    log_access(root, _access("judged-1", "read", memory_id="real-note"))
    log_access(root, _access("eval-other", "search", query="不该出现"))

    captured: dict = {}

    class CaptureHost:
        def __init__(self, events): self._events = events

        async def send(self, prompt: str):
            captured["prompt"] = prompt
            for ev in self._events:
                yield ev

        async def close(self): pass

    def factory(session, workdir):
        (workdir / "judgement-draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return CaptureHost([FINISHED])

    await judge_session(_session(), "2026-07-16", root, host_factory=factory)
    assert "红队" in captured["prompt"]
    assert "real-note" in captured["prompt"]
    assert "不该出现" not in captured["prompt"]


# ---------- failure isolation (C-2) ----------


async def test_judge_session_returns_none_on_error_event(tmp_path: Path) -> None:
    # an errored agent → None (review's advance_extracted is unaffected — C-2).
    root = tmp_path / "memory"
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([ERROR], _VALID_DRAFT),
    )
    assert report is None


async def test_judge_session_returns_none_on_no_draft(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], draft_text=None),
    )
    assert report is None


async def test_judge_session_returns_none_on_bad_json(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], "{not valid json"),
    )
    assert report is None


# ---------- CCHost construction (C-3 eval kind) ----------


async def test_judge_session_cchost_eval_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real CCHost path (host_factory=None) builds with session_kind='eval'
    (C-3 isolation) + the memory MCP attached. monkeypatch CCHost so no real cc
    spawns."""
    captured: dict = {}
    root = tmp_path / "memory"

    class FakeCCHost:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self._workdir = str(kwargs.get("workdir"))

        async def send(self, prompt: str):
            Path(self._workdir, "judgement-draft.json").write_text(
                _VALID_DRAFT, encoding="utf-8"
            )
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr("trowel_py.cc_host.service.CCHost", FakeCCHost)
    report = await judge_session(_session(), "2026-07-16", root)
    assert report is not None
    assert captured["session_kind"] == "eval"
    assert captured.get("mcp_config")  # memory MCP attached


async def test_judge_session_no_access_log_still_works(tmp_path: Path) -> None:
    # a session with zero retrieval history → summary says so, judging still runs.
    root = tmp_path / "memory"
    _seed_real_notes(root, ("real-note", "real-note-2"))
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert report is not None  # empty access-log is fine, not a failure


def test_summarize_pulls_pre_init_records_via_binding(tmp_path: Path) -> None:
    """C-3: judge gathers access records written before cc init (empty
    cc_session_id) once the trowel binding is persisted."""
    from trowel_py.memory.attribution import AttributionIndex
    from trowel_py.memory.judge import _summarize_access_log
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    root = tmp_path / "memory"
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id="cc-x", workdir="/p", date="2026-07-16",
                registered_at="t", session_kind="user", trowel_session_id="t1",
            )
        )
    finally:
        conn.close()
    # pre-init search: cc_session_id empty, resolves via the t1→cc-x binding
    log_access(
        root,
        AccessRecord(
            ts="t", trowel_session_id="t1", cc_session_id="",
            toolUseId="tu-1", action="search", search_id="s1",
            query="how to X", memory_id="m1", rank=0,
        ),
    )
    # post-init read: cc_session_id present
    log_access(
        root,
        AccessRecord(
            ts="t", trowel_session_id="t1", cc_session_id="cc-x",
            toolUseId="tu-2", action="read", search_id="", read_id="r1",
            memory_id="m1",
        ),
    )
    # unrelated session — must be excluded
    log_access(
        root,
        AccessRecord(
            ts="t", trowel_session_id="t-other", cc_session_id="cc-other",
            toolUseId="tu-3", action="search", search_id="s2",
            query="unrelated query", memory_id="m2", rank=0,
        ),
    )
    index = AttributionIndex.from_root(root)
    summary = _summarize_access_log(root, "cc-x", index)
    assert "how to X" in summary
    assert "m1" in summary
    assert "unrelated query" not in summary
