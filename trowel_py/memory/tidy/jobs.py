"""编排周级和月级 Tidy，并提供 Note 退休与 Core 候选辅助操作。"""

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
    """在 Tidy 后收敛 Dictionary，并把所有异常转成 stale 报告。

    Args:
        root: 记忆目录。
        provider: Dictionary 重建使用的模型提供者。

    Returns:
        Dictionary 检查结果；失败时返回含错误文本的 ``stale`` 状态。
    """
    from trowel_py.memory.dictionary import ensure_dictionary_consistent

    try:
        return ensure_dictionary_consistent(root, provider)
    except Exception as exc:  # noqa: BLE001 - Dictionary 失败不得中断已完成的 Tidy
        logger.warning("dictionary ensure failed after tidy: %s", exc)
        return {"dictionary_status": "stale", "error": str(exc)}


def run_weekly_tidy(
    root: Path | str, iso_week: str, provider: LLMProvider
) -> dict[str, Any]:
    """在同一 Tidy 锁内执行指定周的维护流程。

    流程依次重算 Note 计数、生成周记、生成并执行整理计划，最后收敛
    Dictionary。计划执行异常会记录在返回值的 ``tidy`` 子报告中，Dictionary
    异常会转成 ``stale`` 报告；其余阶段的异常原样传播。流程中有
    ``BlockingIOError`` 冒泡时返回 ``skipped``，不再执行后续阶段。

    Args:
        root: 记忆目录。
        iso_week: ISO 周标识，例如 ``2026-W28``；本函数不预先校验格式。
        provider: 周记、整理计划和 Dictionary 重建共用的模型提供者。

    Returns:
        压缩、整理和 Dictionary 报告；流程因 ``BlockingIOError`` 跳过时只
        返回计划 ID 和 ``skipped`` 原因。
    """
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
                except Exception as exc:  # noqa: BLE001 - 计划失败要转成 Tidy 子报告
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
    """为长期未引用或有害反馈过多的活跃 Note 生成退休操作。

    只有 ``last_ref`` 早于 90 天截止日才按未使用退休；空值、非法日期和恰好
    位于截止日的记录不满足该条件。有害反馈达到阈值时不受 ``last_ref``
    影响。非活跃或缺少 ``memory_id`` 的 Note 会被跳过。

    Args:
        root: 记忆目录。
        today_str: 计算截止日使用的 ISO 日期。

    Returns:
        按 Note 读取顺序排列的退休操作。

    Raises:
        ValueError: ``today_str`` 不是合法 ISO 日期。
    """
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
    """按晋升策略评估 Note 并写入 Core 候选文件。

    Args:
        root: 记忆目录。
        policy: 晋升策略；省略时使用默认策略。
        local_tz: 统计独立日期时使用的时区。
        today: 候选生成或阻断元数据使用的 ISO 日期；``None`` 或空字符串时
            使用本机当天日期。

    Returns:
        本次通过晋升门禁且可安全用作文件名的记忆 ID 列表；对应候选文件会被
        创建或覆盖。

    该流程不会直接修改 ``core.md``，但会读取访问日志和会话库，并创建、覆盖
    或阻断候选文件。
    """
    from trowel_py.memory.promotion import evaluate_promotion

    report = evaluate_promotion(
        root, policy or default_policy(), local_tz=local_tz, today=today
    )
    return list(report["candidates"])


def _write_candidate(root: Path, note: Any) -> Path:
    """覆盖写入一份人工提名候选文件。

    文件名直接使用 ``note.memory_id``，内容标记为 ``manual-nominate``，不冒充
    自动晋升的完整证据。调用方负责提供所需的 Note 属性和安全的记忆 ID。

    Args:
        root: 记忆目录。
        note: 提供候选元数据和正文的 Note 对象。

    Returns:
        写入的候选文件路径。
    """
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
    """在同一 Tidy 锁内执行指定月的维护流程。

    流程依次重算计数、生成退休操作、评估晋升、生成月记，再把退休操作与模型
    计划合并执行，最后收敛 Dictionary。计划执行异常会记录在返回值的
    ``tidy`` 子报告中，Dictionary 异常会转成 ``stale`` 报告；此前阶段的
    副作用不会因此撤销。其余阶段的异常原样传播。流程中有
    ``BlockingIOError`` 冒泡时返回 ``skipped``，不再执行后续阶段。

    Args:
        root: 记忆目录。
        month: 月份标识，例如 ``2026-07``；本函数不预先校验格式。
        provider: 月记、整理计划和 Dictionary 重建共用的模型提供者。
        today: 退休与晋升评估使用的 ISO 日期；``None`` 或空字符串时使用
            本机当天日期。

    Returns:
        各阶段报告、候选 ID 和退休操作数；流程因 ``BlockingIOError`` 跳过时
        只返回计划 ID 和 ``skipped`` 原因。
    """
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
                except Exception as exc:  # noqa: BLE001 - 计划失败要转成 Tidy 子报告
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
