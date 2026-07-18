"""slice-067 安全重校准 tests: plan + shadow replay + zero-pollution invariants.

memory_root is tmp_path/"memory" — never touches the real ``~/.trowel/memory``.
The cc host is injected via ``host_factory`` so no real cc spawns (#46416).
Covers spec §通过标准 §shadow replay: plan is read-only; --run needs explicit
scope + proxy; fake-host replay produces staging + report; a failed session
marks the run incomplete; live profile/queue/watermark/sessions.db/jsonl stay
byte-identical; the baseline restores the live files (or records missing).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trowel_py.memory.profile_recalibrate import (
    RecalibrationScopeError,
    plan_recalibration,
    run_recalibration,
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


class _FakeHost:
    def __init__(self, events: list) -> None:
        self._events = events

    async def send(self, prompt: str):
        for ev in self._events:
            yield ev

    async def close(self) -> None:
        pass


def _factory(draft_text: str | None, events: list | None = None):
    """Build a host_factory that writes draft_text into the workdir before running."""

    def factory(session: SessionRecord, workdir: Path) -> _FakeHost:
        if draft_text is not None:
            (workdir / "suggestions-draft.json").write_text(draft_text, encoding="utf-8")
        return _FakeHost(events or [FINISHED])

    return factory


def _seed_session(
    root: Path,
    sid: str,
    *,
    completed: int = 1000,
    jsonl_path: str = "",
    kind: str = "user",
    registered_at: str = "2026-07-14T10:00:00",
    date: str = "2026-07-14",
) -> None:
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.register(
            SessionRecord(
                cc_session_id=sid,
                workdir="/proj",
                date=date,
                jsonl_path=jsonl_path,
                registered_at=registered_at,
            )
        )
        repo.update_completed(sid, completed)
        if kind != "user":
            conn.execute(
                "UPDATE sessions SET session_kind=? WHERE cc_session_id=?",
                (kind, sid),
            )
            conn.commit()
    finally:
        conn.close()


def _seed_live_files(root: Path) -> None:
    """Write the three live files a run baselines."""
    (root / "profile.md").write_text(
        "---\nupdated: 2026-07-01\nsource: user-edit\n---\n## 能力水平\n旧内容\n",
        encoding="utf-8",
    )
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps({"suggestions": [], "updated": "2026-07-01"}),
        encoding="utf-8",
    )
    (root / "meta" / "profile-distill-state.json").write_text(
        json.dumps({"processed": []}),
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _live_hashes(root: Path, jsonl_paths: list[Path]) -> dict[str, str]:
    """sha256 of every live file a run must leave untouched (C-8)."""
    out = {
        "profile.md": _sha(root / "profile.md"),
        "suggestions": _sha(root / "meta" / "profile-suggestions.json"),
        "watermark": _sha(root / "meta" / "profile-distill-state.json"),
        "sessions.db": _sha(root / "meta" / "sessions.db"),
    }
    for p in jsonl_paths:
        out[f"jsonl:{p.name}"] = _sha(p)
    return out


# ----------------------------- plan: scope -----------------------------


def test_plan_requires_explicit_scope(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(RecalibrationScopeError):
        plan_recalibration(root, scope_all=False, from_date=None)


def test_plan_rejects_both_scope_modes(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(RecalibrationScopeError):
        plan_recalibration(root, scope_all=True, from_date="2026-07-01")


def test_plan_from_date_filters_sessions(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_session(root, "old", date="2026-06-01", registered_at="2026-06-01T10:00:00")
    _seed_session(root, "new", date="2026-07-10", registered_at="2026-07-10T10:00:00")
    plan = plan_recalibration(root, scope_all=False, from_date="2026-07-01")
    assert [s.cc_session_id for s in plan.sessions] == ["new"]


# ----------------------------- plan: read-only -----------------------------


def test_plan_does_not_create_staging(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    plan_recalibration(root, scope_all=True)
    assert not (root / "meta" / "profile-recalibration").exists()


def test_plan_on_fresh_root_does_not_create_sessions_db(tmp_path: Path) -> None:
    # codex P2: plan must be read-only — never materialize sessions.db on a
    # fresh root, even though open_sessions_db() would create it.
    root = tmp_path / "memory"
    root.mkdir()
    plan = plan_recalibration(root, scope_all=True)
    assert plan.sessions == ()
    assert plan.estimated_agent_calls == 0
    assert not (root / "meta" / "sessions.db").exists()
    assert not (root / "meta").exists()


def test_plan_leaves_live_files_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", jsonl_path=str(jsonl))
    before = _live_hashes(root, [jsonl])
    plan_recalibration(root, scope_all=True)
    assert _live_hashes(root, [jsonl]) == before


# ----------------------------- plan: freezing -----------------------------


def test_plan_freezes_user_sessions_and_offsets(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=1234, jsonl_path=str(jsonl))
    plan = plan_recalibration(root, scope_all=True)
    [frozen] = plan.sessions
    assert frozen.cc_session_id == "s1"
    assert frozen.end_offset == 1234
    assert frozen.jsonl_exists is True
    assert plan.estimated_agent_calls == 1


def test_plan_excludes_review_distill_eval_kinds(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_session(root, "user")
    _seed_session(root, "rev", kind="review")
    _seed_session(root, "dist", kind="distill")
    _seed_session(root, "eval", kind="eval")
    plan = plan_recalibration(root, scope_all=True)
    assert [s.cc_session_id for s in plan.sessions] == ["user"]


def test_plan_reports_missing_jsonl(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_session(root, "gone", jsonl_path="/does/not/exist.jsonl")
    plan = plan_recalibration(root, scope_all=True)
    assert "gone" in plan.missing_jsonl
    assert plan.estimated_agent_calls == 0


def test_plan_hashes_live_files_marks_missing(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "profile.md").write_text("only profile", encoding="utf-8")
    # suggestions + watermark absent → both "missing"
    plan = plan_recalibration(root, scope_all=True)
    assert plan.live_hashes.profile is not None
    assert plan.live_hashes.suggestions is None
    assert plan.live_hashes.watermark is None
    assert plan.live_hashes.to_manifest_dict()["watermark"] == "missing"


# ----------------------------- run: guards -----------------------------


async def test_run_requires_explicit_scope(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(RecalibrationScopeError):
        await run_recalibration(
            root, scope_all=False, from_date=None, proxy_base_url="http://x"
        )


async def test_run_requires_proxy(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(ValueError):
        await run_recalibration(
            root, scope_all=True, from_date=None, proxy_base_url=""
        )


# ----------------------------- run: happy path -----------------------------


async def test_run_produces_staging_and_report(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=_factory(_VALID_DRAFT),
        run_id="run-1",
        created_at="2026-07-17T02:00:00",
    )
    assert result.status == "complete"
    assert result.sessions_ok == 1
    assert result.sessions_failed == 0
    assert result.accepted_count == 1
    assert result.policy_version == 2
    assert result.by_dimension == {"ability": 1}

    staging = root / "meta" / "profile-recalibration" / "run-1"
    assert (staging / "manifest.json").exists()
    assert (staging / "staged-suggestions.json").exists()
    assert (staging / "report.json").exists()
    assert (staging / "baseline" / "profile.md").exists()

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-1"
    assert manifest["policy_version"] == 2
    assert manifest["status"] == "complete"
    assert manifest["scope"] == {"all": True, "from": None}
    assert manifest["source_hashes"]["profile"] != "missing"

    staged = json.loads((staging / "staged-suggestions.json").read_text(encoding="utf-8"))
    assert len(staged["suggestions"]) == 1
    assert staged["suggestions"][0]["policy_version"] == 2
    assert staged["suggestions"][0]["body"] == "网安硕士 / 红队背景"

    report = json.loads((staging / "report.json").read_text(encoding="utf-8"))
    assert report["raw_count"] == 1
    assert report["accepted_count"] == 1
    assert report["body_max_chars"] == len("网安硕士 / 红队背景")


# ----------------------------- run: failure → incomplete -----------------------------


async def test_run_marks_incomplete_on_session_failure(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl1 = tmp_path / "s1.jsonl"
    jsonl1.write_text("payload", encoding="utf-8")
    jsonl2 = tmp_path / "s2.jsonl"
    jsonl2.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl1))
    _seed_session(
        root, "s2", completed=500, jsonl_path=str(jsonl2), registered_at="2026-07-15T10:00:00"
    )

    # s1 fails (no draft written → DistillError), s2 succeeds
    def factory(session: SessionRecord, workdir: Path) -> _FakeHost:
        if session.cc_session_id == "s2":
            (workdir / "suggestions-draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
            return _FakeHost([FINISHED])
        return _FakeHost([FINISHED])  # s1: finished but no draft → DistillError

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=factory,
        run_id="run-2",
        created_at="2026-07-17T02:00:00",
    )
    assert result.status == "incomplete"
    assert "s1" in result.failed_session_ids
    assert result.sessions_failed == 1
    assert result.sessions_ok == 1
    # the failed session contributed nothing, the ok one still landed
    assert result.accepted_count == 1


# ----------------------------- run: zero pollution (C-8) -----------------------------


async def test_run_leaves_live_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    before = _live_hashes(root, [jsonl])
    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=_factory(_VALID_DRAFT),
        run_id="run-3",
        created_at="2026-07-17T02:00:00",
    )
    assert _live_hashes(root, [jsonl]) == before


# ----------------------------- run: baseline restore -----------------------------


async def test_run_baseline_restores_live_files(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=_factory(_VALID_DRAFT),
        run_id="run-4",
        created_at="2026-07-17T02:00:00",
    )
    baseline = root / "meta" / "profile-recalibration" / "run-4" / "baseline"
    assert _sha(baseline / "profile.md") == _sha(root / "profile.md")
    assert _sha(baseline / "profile-suggestions.json") == _sha(
        root / "meta" / "profile-suggestions.json"
    )
    assert _sha(baseline / "profile-distill-state.json") == _sha(
        root / "meta" / "profile-distill-state.json"
    )


async def test_run_manifest_records_missing_baseline(tmp_path: Path) -> None:
    # only profile.md exists; suggestions + watermark absent
    root = tmp_path / "memory"
    root.mkdir()
    (root / "profile.md").write_text("only profile", encoding="utf-8")
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=_factory(_VALID_DRAFT),
        run_id="run-5",
        created_at="2026-07-17T02:00:00",
    )
    staging = root / "meta" / "profile-recalibration" / "run-5"
    baseline = staging / "baseline"
    # missing files are NOT fabricated
    assert not (baseline / "profile-suggestions.json").exists()
    assert not (baseline / "profile-distill-state.json").exists()
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_hashes"]["suggestions"] == "missing"
    assert manifest["source_hashes"]["watermark"] == "missing"
    assert manifest["source_hashes"]["profile"] != "missing"


# ----------------------------- run: dedup against staging only -----------------------------


async def test_run_dedups_against_staging_only_not_live_queue(tmp_path: Path) -> None:
    # §4: replay dedups against THIS run's staging, never the live v1 queue.
    # Seed a live v1 pending suggestion; the replay must still propose the
    # same-theme v2 body (it is not suppressed by the live queue).
    root = tmp_path / "memory"
    root.mkdir()
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "v1-old",
                        "dimension": "methodology",
                        "body": "一条长 v1 methodology 描述带例子和评价",
                        "sources": ["old"],
                        "date": "2026-07-01",
                        "status": "pending",
                    }
                ],
                "updated": "2026-07-01",
            }
        ),
        encoding="utf-8",
    )
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    draft = json.dumps(
        {
            "suggestions": [
                {
                    "dimension": "methodology",
                    "body": "commit 要让外行看懂",
                    "sources": ["用户原话"],
                    "rationale": "明确表述为通用原则",
                }
            ]
        }
    )
    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=_factory(draft),
        run_id="run-6",
        created_at="2026-07-17T02:00:00",
    )
    assert result.accepted_count == 1
    assert result.staged_suggestions[0].body == "commit 要让外行看懂"
    # the live v1 queue is untouched
    live = json.loads(
        (root / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    )
    assert [s["id"] for s in live["suggestions"]] == ["v1-old"]


# ----------------------------- run: unexpected error → incomplete (W1) -----------------------------


class _RaisingHost:
    """A host whose send() raises a non-DistillError (e.g. proxy network error)."""

    async def send(self, prompt: str):
        raise RuntimeError("proxy exploded")
        yield  # makes send an async generator so `async for` drives it  # noqa

    async def close(self) -> None:
        pass


async def test_run_marks_incomplete_on_unexpected_exception(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    def factory(session: SessionRecord, workdir: Path) -> _RaisingHost:
        return _RaisingHost()

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=factory,
        run_id="run-err",
        created_at="2026-07-17T02:00:00",
    )
    # W1: a non-DistillError must NOT leave the manifest stuck at "running";
    # the run finishes incomplete with the failed id visible + artifacts written.
    assert result.status == "incomplete"
    assert "s1" in result.failed_session_ids
    staging = root / "meta" / "profile-recalibration" / "run-err"
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "incomplete"
    assert (staging / "report.json").exists()


# ----------------------------- run: live change detection (W3) -----------------------------


async def test_run_marks_incomplete_when_live_changes_under_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    def factory(session: SessionRecord, workdir: Path) -> _FakeHost:
        (workdir / "suggestions-draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        # simulate a concurrent write to a live file mid-run (daily distill / UI)
        (root / "profile.md").write_text(
            "---\nupdated: 2026-07-17\nsource: user-edit\n---\n## 能力水平\n改了\n",
            encoding="utf-8",
        )
        return _FakeHost([FINISHED])

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=factory,
        run_id="run-chg",
        created_at="2026-07-17T02:00:00",
    )
    assert result.status == "incomplete"
    staging = root / "meta" / "profile-recalibration" / "run-chg"
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["live_changed_during_run"] is True


# ----------------------------- run: real CCHost uses null registrar (P1-a) -----------------------------


async def test_run_real_cchost_gets_null_registrar(
    tmp_path: Path, monkeypatch
) -> None:
    """codex P1-a: a real-CCHost replay (host_factory=None) must pass a no-op
    registrar so CCHost does NOT write the live sessions.db (C-8). The fake
    CCHost captures its kwargs; we assert session_registrar is the no-op, not
    the None default that would resolve + write the live db."""
    import trowel_py.cc_host.service as svcmod

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

    monkeypatch.setattr(svcmod, "CCHost", FakeCCHost)

    root = tmp_path / "memory"
    root.mkdir()
    _seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    _seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    before_db = _sha(root / "meta" / "sessions.db")
    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://127.0.0.1:8000",
        settings_path="/home/u/.claude/settings.json",
        run_id="run-null",
        created_at="2026-07-17T02:00:00",
    )
    # the registrar passed to CCHost is the no-op, NOT None (which would write live)
    reg = captured.get("session_registrar")
    assert reg is not None
    assert captured["proxy_base_url"] == "http://127.0.0.1:8000"
    assert captured["session_kind"] == "distill"
    # the no-op registrar writes nothing when called
    reg.register(SessionRecord(cc_session_id="x", workdir="", date=""))
    reg.update_completed("x", 999)
    # and the live sessions.db is byte-identical (the replay registered nothing)
    assert _sha(root / "meta" / "sessions.db") == before_db
