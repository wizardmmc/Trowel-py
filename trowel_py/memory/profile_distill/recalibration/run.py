"""画像重校准的隔离重放与产物落盘。"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.memory.profile_distill.prompt import build_distill_prompt
from trowel_py.memory.profile_distill_job import DistillError, GateStats, drive_and_gate
from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    suggestion_to_dict,
)
from trowel_py.memory.sessions_repo import SessionRecord
from trowel_py.memory.types import Profile, Suggestion

from .models import (
    _BASELINE_DIR,
    _LIVE_PROFILE,
    _LIVE_SUGGESTIONS,
    _LIVE_WATERMARK,
    _MANIFEST_FILE,
    _META_DIR,
    _NULL_REGISTRAR,
    _RECALIBRATION_DIR,
    _REPORT_FILE,
    _STAGED_FILE,
    _WORK_DIR,
    FrozenSession,
    LiveHashes,
    RecalibrationRunResult,
    ReplayHostFactory,
)
from .plan import _live_hashes, _validate_scope, plan_recalibration

logger = logging.getLogger("trowel_py.memory.profile_recalibrate")


def _copy_live_to_baseline(root: Path, baseline: Path) -> None:
    """复制现存 live 文件，缺失文件保持缺失。"""
    baseline.mkdir(parents=True, exist_ok=True)
    for _name, rel in (_LIVE_PROFILE, _LIVE_SUGGESTIONS, _LIVE_WATERMARK):
        src = root / rel
        if src.exists():
            shutil.copy2(src, baseline / _name)


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
    """聚合逐会话结果、门禁统计与 staged suggestions。"""
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
    """在隔离 staging 目录中重放冻结的用户会话。"""
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

    # 先写 running，避免中断的重放被误认为已完成。
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
            # 来源缺失会记入报告，但不是重放处理失败。
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
            list(staged_so_far),
            Profile(),
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
                # 真实 CCHost 必须使用空 registrar，避免污染 sessions.db。
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
        except Exception as exc:  # noqa: BLE001
            # 未预期异常也要完成产物落盘，不能让 manifest 停在 running。
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
