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
    parse_and_gate_draft,
    run_daily_distill,
    run_one_session,
)
from trowel_py.memory.profile_distill.state import (
    load_processed,
    mark_processed,
)
from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    load_suggestions,
)
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


# ---------- slice-067: structure gate (parse_and_gate_draft) ----------
# Pure-fn tests for the deterministic Python gates that backstop the prompt.
# Spec 通过标准 §结构 gate: 61→drop, 60→keep; empty body/sources/non-list
# sources dropped; 3 valid → keep 2 + over_limit=1; unknown dim / bad JSON /
# missing draft do NOT advance the watermark; all-dropped still advances.


def _draft(items: list) -> str:
    return json.dumps({"suggestions": items})


def _item(body: str = "短结论", sources: list | None = None, dim: str = "ability") -> dict:
    return {
        "dimension": dim,
        "body": body,
        "sources": ["用户原话"] if sources is None else sources,
        "rationale": "证据",
    }


def test_gate_keeps_60_drops_61() -> None:
    body60 = "字" * 60
    body61 = "字" * 61
    gated = parse_and_gate_draft(
        _draft([_item(body60), _item(body61)]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert [s.body for s in gated.accepted] == [body60]
    assert gated.stats.dropped_too_long == 1
    assert gated.stats.accepted == 1


def test_gate_drops_empty_body() -> None:
    gated = parse_and_gate_draft(
        _draft([_item(body="   "), _item(body="有结论")]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert len(gated.accepted) == 1
    assert gated.stats.dropped_empty_body == 1


def test_gate_drops_empty_sources() -> None:
    gated = parse_and_gate_draft(
        _draft([_item(body="有结论", sources=[])]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.dropped_no_evidence == 1


def test_gate_drops_non_list_sources() -> None:
    gated = parse_and_gate_draft(
        _draft([_item(body="有结论", sources="用户原话")]),  # str, not list
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.dropped_no_evidence == 1


def test_gate_drops_session_id_only_sources() -> None:
    # cc_session_id is traceability, not evidence (§2) — a suggestion whose
    # only non-empty source is the session id is unsupported.
    gated = parse_and_gate_draft(
        _draft([_item(body="有结论", sources=["cc1"])]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.dropped_no_evidence == 1


def test_gate_caps_at_two_records_over_limit() -> None:
    # 3 valid → keep the model's top 2; over_limit=1
    gated = parse_and_gate_draft(
        _draft([_item(body="一"), _item(body="二"), _item(body="三")]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert [s.body for s in gated.accepted] == ["一", "二"]
    assert gated.stats.over_limit == 1
    assert gated.stats.accepted == 2
    assert gated.stats.raw == 3


def test_gate_stamps_policy_version_2() -> None:
    gated = parse_and_gate_draft(
        _draft([_item()]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted[0].policy_version == PROFILE_DISTILL_POLICY_VERSION
    assert gated.accepted[0].status == "pending"
    assert gated.accepted[0].date == "2026-07-17"
    assert gated.accepted[0].id  # uuid stamped


def test_gate_unknown_dimension_raises() -> None:
    with pytest.raises(DistillError):
        parse_and_gate_draft(
            _draft([_item(dim="personality")]),
            cc_session_id="cc1",
            date_str="2026-07-17",
        )


def test_gate_bad_json_raises() -> None:
    with pytest.raises(DistillError):
        parse_and_gate_draft("{not json", cc_session_id="cc1", date_str="2026-07-17")


def test_gate_non_list_suggestions_raises() -> None:
    # top-level schema invalid → whole draft invalid (does not advance)
    with pytest.raises(DistillError):
        parse_and_gate_draft(
            json.dumps({"suggestions": {"dimension": "ability"}}),
            cc_session_id="cc1",
            date_str="2026-07-17",
        )


def test_gate_all_dropped_returns_empty_no_raise() -> None:
    # every candidate fails a gate, but the draft is structurally valid →
    # empty result, no raise (the segment may still advance the watermark).
    gated = parse_and_gate_draft(
        _draft([_item(body=""), _item(body="x" * 61), _item(sources=[])]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.accepted == 0
    assert gated.stats.raw == 3
    assert gated.stats.dropped_empty_body == 1
    assert gated.stats.dropped_too_long == 1
    assert gated.stats.dropped_no_evidence == 1


# ---------- slice-067: watermark advancement boundary ----------


async def test_run_daily_distill_advances_when_all_gated_away(tmp_path: Path) -> None:
    # C-5: a structurally-valid draft whose items all fail the gates STILL
    # advances the watermark (no sticky daily retry of a stable output) and
    # leaves the queue empty.
    root = tmp_path / "memory"
    _seed_session(root, "s1", completed=1000)
    all_too_long = json.dumps(
        {"suggestions": [_item(body="字" * 61)]}
    )
    await run_daily_distill(
        root,
        "http://x",
        host_factory=_factory([FINISHED], all_too_long),
        date_str="2026-07-17",
    )
    assert load_suggestions(root) == []  # nothing landed
    assert load_processed(root)["s1"].end_offset == 1000  # still advanced


async def test_run_daily_distill_bad_dim_does_not_advance(tmp_path: Path) -> None:
    # unknown dimension is a STRUCTURAL failure → not marked → retried next run
    root = tmp_path / "memory"
    _seed_session(root, "s1", completed=1000)
    bad_dim = json.dumps({"suggestions": [_item(dim="personality")]})
    await run_daily_distill(
        root,
        "http://x",
        host_factory=_factory([FINISHED], bad_dim),
        date_str="2026-07-17",
    )
    assert load_processed(root) == {}  # NOT advanced → next run retries
    assert load_suggestions(root) == []


async def test_run_daily_distill_dedup_ignores_v1_queue(tmp_path: Path) -> None:
    # §3: v2 dedup must NOT use v1 queue — a v1 long body on the same theme
    # must not block a shorter v2 proposal. We assert the v2 suggestion still
    # lands despite a same-theme v1 pending record on disk.
    root = tmp_path / "memory"
    _seed_session(root, "s1", completed=1000)
    # seed a v1 pending suggestion (no policy_version field → loads as v1)
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
        {"suggestions": [_item(body="commit 要让外行看懂", dim="methodology")]}
    )
    await run_daily_distill(
        root,
        "http://x",
        host_factory=_factory([FINISHED], v2_draft),
        date_str="2026-07-17",
    )
    loaded = load_suggestions(root)
    # v1 record untouched + v2 record landed alongside it
    assert {s.id for s in loaded} == {"v1-old"} | {
        s.id for s in loaded if s.policy_version == PROFILE_DISTILL_POLICY_VERSION
    }
    v2 = [s for s in loaded if s.policy_version == PROFILE_DISTILL_POLICY_VERSION]
    assert len(v2) == 1
    assert v2[0].body == "commit 要让外行看懂"


async def test_run_one_session_feeds_only_current_policy_queue_to_dedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # I2: directly assert the dedup INPUT excludes v1. Seed v1 + v2 on disk,
    # spy on build_distill_prompt, and verify only policy_version==2 items are
    # passed (the v1 long body must not be fed to the agent's dedup view).
    import trowel_py.memory.profile_distill_job as jobmod

    root = tmp_path / "memory"
    root.mkdir(parents=True, exist_ok=True)
    _seed_session(root, "s1", completed=1000)
    (root / "meta").mkdir(exist_ok=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "v1",
                        "dimension": "ability",
                        "body": "v1 long ability body",
                        "sources": ["old"],
                        "date": "2026-07-01",
                        "status": "pending",
                    },
                    {
                        "id": "v2",
                        "dimension": "goal",
                        "body": "v2 short goal",
                        "sources": ["new"],
                        "date": "2026-07-15",
                        "status": "pending",
                        "policy_version": PROFILE_DISTILL_POLICY_VERSION,
                    },
                ],
                "updated": "2026-07-15",
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, list] = {}
    real_build = jobmod.build_distill_prompt

    def spy(jsonl_path: str, existing, profile, **kw):
        captured["pvs"] = [s.policy_version for s in existing]
        return real_build(jsonl_path, existing, profile, **kw)

    monkeypatch.setattr(jobmod, "build_distill_prompt", spy)
    await run_one_session(
        _session(),
        "2026-07-17",
        root,
        proxy_base_url="http://x",
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert captured["pvs"] == [PROFILE_DISTILL_POLICY_VERSION]
