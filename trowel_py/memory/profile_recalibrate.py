"""slice-067 安全重校准：v2 shadow replay over frozen user sessions.

Provides a read-only ``plan`` and an isolated ``run`` that re-distills history
under the v2 hard rules WITHOUT touching the live profile / suggestion queue /
distill watermark / sessions.db / source jsonl (C-8 shadow 零污染). Output
lands under ``meta/profile-recalibration/<run-id>/`` (baseline + manifest +
staged-suggestions + report). The slice deliberately offers NO auto-promote /
apply (§5): staging is for human review; the user writes a trimmed profile
through the existing Profile UI, which snapshots the prior version.

- ``plan_recalibration``: freezes user sessions + completed offsets, lists
  missing jsonl, and hashes the three live files. No model, no staging, no
  writes.
- ``run_recalibration``: copies the three live files to ``baseline/``, replays
  each session in time order over ``[0, frozen_end_offset)``, dedups ONLY
  against this run's own staging candidates (never v1 queue, never live
  watermark), and writes manifest + staged-suggestions + report.

Replay reuses ``drive_and_gate`` from the daily distill so host / draft / gate
semantics are identical; it differs only in WHERE the prompt's dedup inputs
come from (this run's staging) and WHERE the candidates land (a staging file).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from trowel_py.memory.profile_distill_job import (
    DistillError,
    GateStats,
    drive_and_gate,
)
from trowel_py.memory.profile_distill_prompt import build_distill_prompt
from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    suggestion_to_dict,
)
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db_readonly,
)
from trowel_py.memory.types import Profile, Suggestion

logger = logging.getLogger(__name__)

#: a callable that builds a cc host for one session in its replay workdir.
#: Mirrors profile_distill_job.HostFactory; tests inject a fake so no real cc
#: is spawned (#46416).
ReplayHostFactory = Callable[[SessionRecord, Path], Any]

_META_DIR = "meta"
_RECALIBRATION_DIR = "profile-recalibration"
_BASELINE_DIR = "baseline"
_WORK_DIR = "work"
_MANIFEST_FILE = "manifest.json"
_STAGED_FILE = "staged-suggestions.json"
_REPORT_FILE = "report.json"

#: the three live files a run baselines + must leave byte-identical. profile.md
#: lives at the root; the two JSON files live under meta/.
_LIVE_PROFILE = ("profile.md", Path("profile.md"))
_LIVE_SUGGESTIONS = ("profile-suggestions.json", Path(_META_DIR) / "profile-suggestions.json")
_LIVE_WATERMARK = ("profile-distill-state.json", Path(_META_DIR) / "profile-distill-state.json")

#: only ``user`` completed sessions are replayed (review / distill / eval
#: excluded — §4). find_all_completed_sessions takes an exclude list.
_EXCLUDE_KINDS = ["review", "distill", "eval"]


class RecalibrationScopeError(ValueError):
    """``--all`` / ``--from`` were both given or neither given (§4)."""


@dataclass(frozen=True)
class _NullSessionRegistrar:
    """A SessionRegistrar that writes nothing (codex P1-a / C-8).

    The shadow-replay CCHost gets this so its init/result hooks do NOT register
    the replay agent or stamp completed offsets into ANY sessions.db — without
    it, CCHost's default (None) registrar resolves the live memory root and
    writes the live sessions.db, breaking the zero-pollution guarantee. The
    daily distill deliberately does NOT use this (its agent session is a real
    live event that belongs in sessions.db).
    """

    def register(self, rec: SessionRecord) -> None:
        """No-op: a replay agent never enters any sessions registry."""

    def update_completed(
        self, cc_session_id: str, completed_bytes: int, when: str | None = None
    ) -> None:
        """No-op: a replay agent never stamps any completed watermark."""


#: module-level singleton — the registrar is stateless, so one instance is fine.
_NULL_REGISTRAR = _NullSessionRegistrar()


@dataclass(frozen=True)
class FrozenSession:
    """One user session captured at its completed offset for replay.

    Attributes:
        cc_session_id: cc's session uuid (the jsonl filename stem).
        end_offset: the frozen ``last_completed_offset`` — replay reads
            ``[0, end_offset)``; a tail that grows during the run is ignored.
        jsonl_path: absolute path to the cc session jsonl.
        jsonl_exists: False when the jsonl is missing (a missing source is
            reported, never faked — C-9).
        registered_at: ISO timestamp; replay runs in this order.
    """

    cc_session_id: str
    end_offset: int
    jsonl_path: str
    jsonl_exists: bool
    registered_at: str


@dataclass(frozen=True)
class LiveHashes:
    """sha256 of the three live files at plan time (None = file missing).

    A run re-reads these to prove the live files did not change mid-run, and
    the manifest records them so a later reader can verify the baseline really
    captured the live state (C-8 shadow 零污染).
    """

    profile: str | None
    suggestions: str | None
    watermark: str | None

    def to_manifest_dict(self) -> dict[str, str]:
        return {
            "profile": self.profile or "missing",
            "suggestions": self.suggestions or "missing",
            "watermark": self.watermark or "missing",
        }


@dataclass(frozen=True)
class RecalibrationPlan:
    """Read-only plan: what a run WOULD replay, and the live-state snapshot.

    ``plan_recalibration`` returns this; it never calls a model, never creates
    staging, never writes a live file. ``estimated_agent_calls`` excludes
    sessions whose jsonl is missing (those cannot be replayed — C-9).
    """

    scope_all: bool
    from_date: str | None
    sessions: tuple[FrozenSession, ...]
    missing_jsonl: tuple[str, ...]
    live_hashes: LiveHashes
    estimated_agent_calls: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": {"all": self.scope_all, "from": self.from_date},
            "sessions": [
                {
                    "cc_session_id": s.cc_session_id,
                    "end_offset": s.end_offset,
                    "jsonl_path": s.jsonl_path,
                    "jsonl_exists": s.jsonl_exists,
                    "registered_at": s.registered_at,
                }
                for s in self.sessions
            ],
            "missing_jsonl": list(self.missing_jsonl),
            "live_hashes": self.live_hashes.to_manifest_dict(),
            "estimated_agent_calls": self.estimated_agent_calls,
        }


@dataclass(frozen=True)
class RecalibrationRunResult:
    """Outcome of a shadow replay, mirroring the on-disk report.json."""

    run_id: str
    policy_version: int
    created_at: str
    scope_all: bool
    from_date: str | None
    status: str  # "complete" | "incomplete"
    staging_dir: str
    sessions_total: int
    sessions_ok: int
    sessions_failed: int
    failed_session_ids: tuple[str, ...]
    raw_count: int
    accepted_count: int
    by_dimension: dict[str, int]
    body_avg_chars: float
    body_max_chars: int
    gate_drops: dict[str, int]
    staged_suggestions: tuple[Suggestion, ...]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "policy_version": self.policy_version,
            "created_at": self.created_at,
            "scope": {"all": self.scope_all, "from": self.from_date},
            "status": self.status,
            "sessions_total": self.sessions_total,
            "sessions_ok": self.sessions_ok,
            "sessions_failed": self.sessions_failed,
            "failed_session_ids": list(self.failed_session_ids),
            "raw_count": self.raw_count,
            "accepted_count": self.accepted_count,
            "by_dimension": dict(self.by_dimension),
            "body_avg_chars": self.body_avg_chars,
            "body_max_chars": self.body_max_chars,
            "gate_drops": dict(self.gate_drops),
        }


# -------------------------------------------------------------------- helpers


def _validate_scope(*, scope_all: bool, from_date: str | None) -> None:
    """``--all`` and ``--from`` are mutually exclusive and exactly one required."""
    if scope_all and from_date is not None:
        raise RecalibrationScopeError("specify either --all or --from, not both")
    if not scope_all and from_date is None:
        raise RecalibrationScopeError("must specify --all or --from")


def _sha256_file(path: Path) -> str | None:
    """sha256 hex of a file's bytes, or None if it does not exist (C-9: never fake)."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _live_hashes(root: Path) -> LiveHashes:
    """Hash the three live files at plan/run time (None where a file is missing)."""
    return LiveHashes(
        profile=_sha256_file(root / _LIVE_PROFILE[1]),
        suggestions=_sha256_file(root / _LIVE_SUGGESTIONS[1]),
        watermark=_sha256_file(root / _LIVE_WATERMARK[1]),
    )


def _session_day(session: SessionRecord) -> str:
    """The calendar day a session falls on (date preferred, else registered_at)."""
    return session.date or (session.registered_at[:10] if session.registered_at else "")


def _copy_live_to_baseline(root: Path, baseline: Path) -> None:
    """Copy the three live files into ``baseline/``; a missing file is skipped.

    A missing live file is recorded in the manifest as ``missing`` (via the
    plan's LiveHashes) — we never fabricate an empty file to masquerade as a
    real prior state (C-9 / §4).
    """
    baseline.mkdir(parents=True, exist_ok=True)
    for _name, rel in (_LIVE_PROFILE, _LIVE_SUGGESTIONS, _LIVE_WATERMARK):
        src = root / rel
        if src.exists():
            shutil.copy2(src, baseline / _name)


# -------------------------------------------------------------------- plan


def plan_recalibration(
    root: Path, *, scope_all: bool = False, from_date: str | None = None
) -> RecalibrationPlan:
    """Freeze user sessions + completed offsets; report cost + missing sources.

    Read-only: opens sessions.db (no writes), hashes the three live files. No
    model is called, no staging is created, no live file is written.

    Args:
        root: memory root.
        scope_all: replay every user completed session.
        from_date: replay sessions on/after this ISO ``YYYY-MM-DD`` (exclusive
            with ``scope_all``).

    Raises:
        RecalibrationScopeError: both or neither of ``scope_all`` / ``from_date``.
    """
    _validate_scope(scope_all=scope_all, from_date=from_date)
    # §4 plan is read-only: never create/migrate sessions.db. A missing db =
    # no sessions registered yet → empty plan. A ro connection + migrate=False
    # guarantees no DDL/write even on a stale-schema db (codex P2).
    conn = open_sessions_db_readonly(root)
    if conn is None:
        return RecalibrationPlan(
            scope_all=scope_all,
            from_date=from_date,
            sessions=(),
            missing_jsonl=(),
            live_hashes=_live_hashes(root),
            estimated_agent_calls=0,
        )
    try:
        repo = create_sessions_repository(conn, migrate=False)
        records = repo.find_all_completed_sessions(exclude_kinds=_EXCLUDE_KINDS)
    finally:
        conn.close()

    frozen: list[FrozenSession] = []
    missing: list[str] = []
    for rec in records:
        if not scope_all:
            day = _session_day(rec)
            if not day or day < (from_date or ""):
                continue
        end = rec.last_completed_offset or 0
        jsonl_path = rec.jsonl_path or ""
        exists = bool(jsonl_path) and Path(jsonl_path).exists()
        if not exists:
            missing.append(rec.cc_session_id)
        frozen.append(
            FrozenSession(
                cc_session_id=rec.cc_session_id,
                end_offset=end,
                jsonl_path=jsonl_path,
                jsonl_exists=exists,
                registered_at=rec.registered_at,
            )
        )
    return RecalibrationPlan(
        scope_all=scope_all,
        from_date=from_date,
        sessions=tuple(frozen),
        missing_jsonl=tuple(missing),
        live_hashes=_live_hashes(root),
        estimated_agent_calls=sum(1 for s in frozen if s.jsonl_exists),
    )


# --------------------------------------------------------------------- run


def _manifest(
    *,
    run_id: str,
    created_at: str,
    scope_all: bool,
    from_date: str | None,
    live_hashes: LiveHashes,
    sessions: tuple[FrozenSession, ...],
    status: str,
    live_changed_during_run: bool = False,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "policy_version": PROFILE_DISTILL_POLICY_VERSION,
        "created_at": created_at,
        "scope": {"all": scope_all, "from": from_date},
        "source_hashes": live_hashes.to_manifest_dict(),
        "sessions": [
            {
                "cc_session_id": s.cc_session_id,
                "end_offset": s.end_offset,
                "jsonl_path": s.jsonl_path,
            }
            for s in sessions
        ],
        "status": status,
        # slice-067 W3: True iff a live file changed between run start and end
        # (e.g. concurrent daily distill / UI write). The baseline then captures
        # a stale snapshot; the run is marked incomplete so a reader does not
        # treat staging as authoritative over a live state it did not actually
        # pin. C-8 still holds (recalibrate itself wrote nothing live).
        "live_changed_during_run": live_changed_during_run,
    }


def _aggregate_report(
    *,
    run_id: str,
    created_at: str,
    scope_all: bool,
    from_date: str | None,
    status: str,
    staging_dir: str,
    outcomes: list[tuple[FrozenSession, tuple[Suggestion, ...], GateStats, str]],
) -> RecalibrationRunResult:
    """Fold per-session outcomes into the run-level report + staged list."""
    failed = [s.cc_session_id for (s, _a, _st, err) in outcomes if err]
    ok = sum(1 for (_s, _a, _st, err) in outcomes if not err)
    staged: list[Suggestion] = []
    for _s, accepted, _st, _err in outcomes:
        staged.extend(accepted)

    by_dimension: dict[str, int] = {}
    for s in staged:
        by_dimension[s.dimension] = by_dimension.get(s.dimension, 0) + 1
    body_lens = [len(s.body) for s in staged]
    raw = sum(st.raw for (_s, _a, st, _e) in outcomes)
    gate_drops = {
        "dropped_empty_body": sum(st.dropped_empty_body for (_s, _a, st, _e) in outcomes),
        "dropped_too_long": sum(st.dropped_too_long for (_s, _a, st, _e) in outcomes),
        "dropped_no_evidence": sum(st.dropped_no_evidence for (_s, _a, st, _e) in outcomes),
        "over_limit": sum(st.over_limit for (_s, _a, st, _e) in outcomes),
    }
    return RecalibrationRunResult(
        run_id=run_id,
        policy_version=PROFILE_DISTILL_POLICY_VERSION,
        created_at=created_at,
        scope_all=scope_all,
        from_date=from_date,
        status=status,
        staging_dir=staging_dir,
        sessions_total=len(outcomes),
        sessions_ok=ok,
        sessions_failed=len(failed),
        failed_session_ids=tuple(failed),
        raw_count=raw,
        accepted_count=len(staged),
        by_dimension=by_dimension,
        body_avg_chars=round(sum(body_lens) / len(body_lens), 2) if body_lens else 0.0,
        body_max_chars=max(body_lens) if body_lens else 0,
        gate_drops=gate_drops,
        staged_suggestions=tuple(staged),
    )


async def run_recalibration(
    root: Path,
    *,
    scope_all: bool = False,
    from_date: str | None = None,
    proxy_base_url: str,
    settings_path: Path | str | None = None,
    host_factory: ReplayHostFactory | None = None,
    run_id: str | None = None,
    created_at: str | None = None,
) -> RecalibrationRunResult:
    """Shadow-replay frozen user sessions under v2 into an isolated staging dir.

    Copies the three live files to ``baseline/``, replays each session over
    ``[0, frozen_end_offset)`` in time order (deduping only against THIS run's
    staging candidates), and writes ``manifest.json`` + ``staged-suggestions
    .json`` + ``report.json``. Live profile / queue / watermark / sessions.db /
    jsonl are left byte-identical (C-8). A mid-run session failure marks the run
    ``incomplete`` and records the failed id; remaining sessions still replay
    (§4: keep the failure list, do not pass an incomplete result off as complete).

    Args:
        root: memory root.
        scope_all / from_date: mutually exclusive, exactly one required.
        proxy_base_url: REQUIRED — replay must go through the trowel proxy
            (§4: never bypass slice-030's cache / identity fixes).
        settings_path: ~/.claude/settings.json (re-injects provider vars the
            proxy strips — slice-050 CR [1]).
        host_factory: optional ``(session, workdir) -> cc host``; None → real
            CCHost (tests inject a fake so no real cc spawns).
        run_id / created_at: injected by tests; default a uuid / now().
    """
    _validate_scope(scope_all=scope_all, from_date=from_date)
    if not proxy_base_url:
        raise ValueError("--run requires --proxy-base-url (never bypass the proxy)")

    plan = plan_recalibration(root, scope_all=scope_all, from_date=from_date)
    rid = run_id or uuid.uuid4().hex
    stamp = created_at or datetime.now().isoformat()

    staging = root / _META_DIR / _RECALIBRATION_DIR / rid
    baseline = staging / _BASELINE_DIR
    work_root = staging / _WORK_DIR
    staging.mkdir(parents=True, exist_ok=True)
    _copy_live_to_baseline(root, baseline)

    # write manifest up-front with status=running so an interrupted run is not
    # mistaken for a finished one; rewritten complete/incomplete at the end.
    manifest_path = staging / _MANIFEST_FILE
    manifest_path.write_text(
        json.dumps(
            _manifest(
                run_id=rid,
                created_at=stamp,
                scope_all=scope_all,
                from_date=from_date,
                live_hashes=plan.live_hashes,
                sessions=plan.sessions,
                status="running",
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    outcomes: list[tuple[FrozenSession, tuple[Suggestion, ...], GateStats, str]] = []
    staged_so_far: list[Suggestion] = []
    had_failure = False
    for frozen in plan.sessions:
        if not frozen.jsonl_exists:
            # missing source: reported in plan, cannot replay (C-9). Not a run
            # failure — the source is genuinely gone, not a processing error.
            outcomes.append((frozen, (), GateStats(), "missing jsonl"))
            continue
        session = SessionRecord(
            cc_session_id=frozen.cc_session_id,
            workdir="",
            date="",
            jsonl_path=frozen.jsonl_path,
            registered_at=frozen.registered_at,
        )
        workdir = work_root / frozen.cc_session_id
        workdir.mkdir(parents=True, exist_ok=True)
        prompt = build_distill_prompt(
            frozen.jsonl_path,
            list(staged_so_far),  # dedup against this run's staging only
            Profile(),  # empty — replay does NOT treat live profile as fact
            start_offset=None,
            end_offset=frozen.end_offset,
        )
        try:
            gated = await drive_and_gate(
                session,
                workdir,
                prompt,
                proxy_base_url=proxy_base_url,
                settings_path=settings_path,
                host_factory=host_factory,
                date_str=stamp[:10],
                # C-8 / codex P1-a: the replay CCHost must not touch live
                # sessions.db. (Ignored when host_factory is set — fakes do not
                # build a real CCHost.)
                session_registrar=_NULL_REGISTRAR,
            )
        except DistillError as exc:
            had_failure = True
            logger.warning(
                "recalibrate: session %s failed (run marked incomplete): %s",
                frozen.cc_session_id,
                exc,
            )
            outcomes.append((frozen, (), GateStats(), str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 — never let one session's
            # unexpected error (network, proxy, fake-host bug) leave the manifest
            # stuck at status="running"; record it and finish the run incomplete
            # so the staging artifact + report still land (W1).
            had_failure = True
            logger.exception(
                "recalibrate: unexpected error on %s (run marked incomplete)",
                frozen.cc_session_id,
            )
            outcomes.append((frozen, (), GateStats(), f"unexpected: {exc}"))
            continue
        accepted = gated.accepted
        staged_so_far.extend(accepted)
        outcomes.append((frozen, accepted, gated.stats, ""))

    # W3: prove the live files did not change under us (a concurrent daily
    # distill / UI write would mean the baseline pinned a stale snapshot).
    after_hashes = _live_hashes(root)
    live_changed_during_run = after_hashes != plan.live_hashes
    status = "incomplete" if (had_failure or live_changed_during_run) else "complete"
    result = _aggregate_report(
        run_id=rid,
        created_at=stamp,
        scope_all=scope_all,
        from_date=from_date,
        status=status,
        staging_dir=str(staging),
        outcomes=outcomes,
    )

    # persist manifest (final status) + staged-suggestions + report
    manifest_path.write_text(
        json.dumps(
            _manifest(
                run_id=rid,
                created_at=stamp,
                scope_all=scope_all,
                from_date=from_date,
                live_hashes=plan.live_hashes,
                sessions=plan.sessions,
                status=status,
                live_changed_during_run=live_changed_during_run,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (staging / _STAGED_FILE).write_text(
        json.dumps(
            {"suggestions": [suggestion_to_dict(s) for s in result.staged_suggestions]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (staging / _REPORT_FILE).write_text(
        json.dumps(result.to_report_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
