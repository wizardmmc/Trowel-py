"""周级与月级 tidy 的周期任务编排。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.promotion_policy import PromotionPolicy, default_policy
from trowel_py.memory.store import MemoryStore, _dump_frontmatter

from .apply import _tidy_lock, apply_plan
from .models import TidyOperation, TidyPlan
from .planning import build_monthly_plan, build_tidy_plan

logger = logging.getLogger("trowel_py.memory.tidy")

HALF_LIFE_DAYS = 90
HARMFUL_RETIRE_THRESHOLD = 3
_CANDIDATES_DIR = "meta/core-candidates"


def _ensure_dictionary(root: Path | str, provider: LLMProvider) -> dict[str, Any]:
    """Tidy 后收敛 dictionary；失败只标记 stale，留待下次重试。"""
    from trowel_py.memory.dictionary import ensure_dictionary_consistent

    try:
        return ensure_dictionary_consistent(root, provider)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dictionary ensure failed after tidy: %s", exc)
        return {"dictionary_status": "stale", "error": str(exc)}


def run_weekly_tidy(
    root: Path | str, iso_week: str, provider: LLMProvider
) -> dict[str, Any]:
    """依次重算计数、生成周记、应用计划并收敛 dictionary。"""
    from trowel_py.memory.compress import compress_weekly
    from trowel_py.memory.recompute import recompute_counters

    root_path = Path(root)
    try:
        with _tidy_lock(root_path):
            recompute_counters(root_path)
            compress_report = compress_weekly(root_path, iso_week, provider)
            plan = build_tidy_plan(root_path, iso_week, provider)
            if plan.operations:
                try:
                    tidy_report = apply_plan(root_path, plan)
                except Exception as exc:  # noqa: BLE001
                    tidy_report = {
                        "plan_id": plan.plan_id,
                        "error": str(exc),
                        "applied": [],
                        "operations": len(plan.operations),
                    }
            else:
                tidy_report = {
                    "plan_id": plan.plan_id,
                    "applied": [],
                    "operations": 0,
                }
            dict_report = _ensure_dictionary(root_path, provider)
    except BlockingIOError:
        return {
            "plan_id": f"weekly-{iso_week}",
            "skipped": "another tidy is running",
        }
    return {
        "plan_id": plan.plan_id,
        "compress": compress_report,
        "tidy": tidy_report,
        "dictionary": dict_report,
    }


def plan_retirements(root: Path | str, today_str: str) -> tuple[TidyOperation, ...]:
    """按最后引用时间和 harmful 计数生成确定性的退休操作。"""
    from datetime import date as _date, timedelta

    today = _date.fromisoformat(today_str)
    cutoff = today - timedelta(days=HALF_LIFE_DAYS)
    store = MemoryStore(root)
    ops: list[TidyOperation] = []
    for _stem, note in store.load_notes_with_id():
        if note.status != "active" or not note.memory_id:
            continue
        retire = False
        reason = ""
        if note.last_ref:
            try:
                last = _date.fromisoformat(note.last_ref)
                if last < cutoff:
                    retire = True
                    reason = f"未使用 {HALF_LIFE_DAYS}+ 天（last_ref={note.last_ref}）"
            except ValueError:
                pass
        if note.harmful_refs >= HARMFUL_RETIRE_THRESHOLD:
            retire = True
            reason = (reason + "; " if reason else "") + (
                f"harmful_refs={note.harmful_refs}≥{HARMFUL_RETIRE_THRESHOLD}"
            )
        if retire:
            ops.append(
                TidyOperation(
                    type="retire",
                    target=note.memory_id,
                    reason=reason,
                )
            )
    return tuple(ops)


def promote_candidates(
    root: Path | str,
    *,
    policy: PromotionPolicy | None = None,
    local_tz: Any | None = None,
    today: str | None = None,
) -> list[str]:
    """按统一 promotion 策略写候选文件，不直接修改 core.md。"""
    from trowel_py.memory.promotion import evaluate_promotion

    report = evaluate_promotion(
        root, policy or default_policy(), local_tz=local_tz, today=today
    )
    return list(report["candidates"])


def _write_candidate(root: Path, note: Any) -> Path:
    """为人工提名写候选文件，与自动 promotion 的证据格式保持隔离。"""
    path = root / _CANDIDATES_DIR / f"{note.memory_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "type": "core-candidate",
        "memory_id": note.memory_id,
        "source_title": note.title,
        "helpful_refs": note.helpful_refs,
        "kind": note.kind,
        "verification": note.verification,
        "policy_version": "manual-nominate",
        "status": "candidate",
    }
    body = (
        f"# 候选：{note.title}\n\n{note.summary}\n\n## 正文\n\n{note.body}\n\n"
        "## 晋升理由\n\n人工提名（helpful 证据未达自动策略阈值）。"
    )
    path.write_text(_dump_frontmatter(fm, body), encoding="utf-8")
    return path


def run_monthly_tidy(
    root: Path | str,
    month: str,
    provider: LLMProvider,
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """执行月度重算、退休、晋升、压缩、计划应用与索引收敛。"""
    from datetime import date as _date

    from trowel_py.memory.compress import compress_monthly
    from trowel_py.memory.recompute import recompute_counters

    root_path = Path(root)
    today_str = today or _date.today().isoformat()
    try:
        with _tidy_lock(root_path):
            recompute_report = recompute_counters(root_path)
            retire_ops = plan_retirements(root_path, today_str)
            from trowel_py.memory.promotion import evaluate_promotion

            promotion_report = evaluate_promotion(
                root_path, default_policy(), today=today_str
            )
            promoted = promotion_report["candidates"]
            compress_report = compress_monthly(root_path, month, provider)
            plan = build_monthly_plan(root_path, month, provider)
            merged_plan = TidyPlan(
                plan_id=plan.plan_id,
                source_snapshot=plan.source_snapshot,
                operations=retire_ops + plan.operations,
                core_candidates=tuple(promoted),
            )
            if merged_plan.operations:
                try:
                    tidy_report = apply_plan(root_path, merged_plan)
                except Exception as exc:  # noqa: BLE001
                    tidy_report = {
                        "plan_id": merged_plan.plan_id,
                        "error": str(exc),
                        "applied": [],
                        "operations": len(merged_plan.operations),
                    }
            else:
                tidy_report = {
                    "plan_id": merged_plan.plan_id,
                    "applied": [],
                    "operations": 0,
                }
            dict_report = _ensure_dictionary(root_path, provider)
    except BlockingIOError:
        return {
            "plan_id": f"monthly-{month}",
            "skipped": "another tidy is running",
        }
    return {
        "plan_id": merged_plan.plan_id,
        "compress": compress_report,
        "tidy": tidy_report,
        "recompute": recompute_report,
        "promotion": promotion_report,
        "promoted": promoted,
        "retire_ops": len(retire_ops),
        "dictionary": dict_report,
    }
