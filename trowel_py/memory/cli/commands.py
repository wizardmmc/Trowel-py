"""Memory 非 tidy 子命令。"""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from collections.abc import Callable
from datetime import date
from pathlib import Path


def current_iso_week() -> str:
    """返回当前 ISO 周。"""
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


def current_month() -> str:
    """返回当前月份。"""
    return date.today().strftime("%Y-%m")


def _run_dictionary_command(
    args: Namespace,
    root: Path,
    ensure_dict_fn: Callable[[Path], None],
) -> int:
    if args.cmd == "dict-rebuild":
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider
        from trowel_py.memory.dictionary import rebuild_dictionary

        provider = AnthropicProvider(load_llm_config())
        result = rebuild_dictionary(root, apply=args.apply, provider=provider)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "dict-check":
        from trowel_py.memory.dictionary_check import check_dictionary

        print(json.dumps(check_dictionary(root), ensure_ascii=False, indent=2))
        return 0

    from trowel_py.memory.migrate import migrate_memory

    report = migrate_memory(root, apply=args.apply)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"[memory] migrate {mode}: scanned={report.scanned} "
        f"migrated={report.migrated} skipped={report.skipped}"
    )
    if report.backed_up:
        print(f"[memory] backup -> {report.backed_up}")
    if args.apply:
        ensure_dict_fn(root)
    return 0


def _run_core_command(args: Namespace, root: Path) -> int:
    from trowel_py.memory.core_ops import (
        activate_core_item,
        approve_candidate,
        nominate_candidate,
    )

    if args.core_cmd == "nominate":
        memory_id = nominate_candidate(root, args.note_stem)
        print(f"[memory] nominated {args.note_stem} -> candidate {memory_id}")
    elif args.core_cmd == "approve":
        approve_candidate(root, args.candidate_id)
        print(f"[memory] approved {args.candidate_id} -> core.md (trial)")
    elif args.core_cmd == "activate":
        activate_core_item(root, args.memory_id)
        print(f"[memory] activated {args.memory_id} -> core (active)")
    return 0


def _run_metrics(root: Path) -> int:
    from trowel_py.memory.north_star import compute_north_star, memory_usage_metrics

    report = {
        "north_star": compute_north_star(root),
        "usage": memory_usage_metrics(root),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _run_promotion(args: Namespace, root: Path) -> int:
    from trowel_py.memory.promotion import evaluate_promotion
    from trowel_py.memory.promotion_policy import PromotionPolicy, load_policy

    policy = load_policy(args.policy) if args.policy else PromotionPolicy()
    report = evaluate_promotion(root, policy, dry_run=not args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def _run_profile_recalibration(args: Namespace, root: Path) -> int:
    from trowel_py.memory.profile_recalibrate import (
        RecalibrationRunResult,
        RecalibrationScopeError,
        plan_recalibration,
        run_recalibration,
    )

    if args.run:
        if not args.proxy_base_url:
            print("[memory] profile-recalibrate --run needs --proxy-base-url")
            return 2
        # 代理会剥离 provider 环境变量，必须显式传入 Claude settings。
        settings_path = Path.home() / ".claude" / "settings.json"
        try:
            run_result: RecalibrationRunResult = asyncio.run(
                run_recalibration(
                    root,
                    scope_all=args.all,
                    from_date=args.from_date,
                    proxy_base_url=args.proxy_base_url,
                    settings_path=settings_path,
                )
            )
        except RecalibrationScopeError as exc:
            print(f"[memory] profile-recalibrate: {exc}")
            return 2
        print(json.dumps(run_result.to_report_dict(), ensure_ascii=False, indent=2))
        return 0
    try:
        plan = plan_recalibration(
            root,
            scope_all=args.all,
            from_date=args.from_date,
        )
    except RecalibrationScopeError as exc:
        print(f"[memory] profile-recalibrate: {exc}")
        return 2
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


def run_domain_command(
    args: Namespace,
    root: Path,
    *,
    ensure_dict_fn: Callable[[Path], None],
) -> int:
    """执行不依赖根 CLI 回调的领域命令。"""
    if args.cmd in {"dict-rebuild", "dict-check", "migrate"}:
        return _run_dictionary_command(args, root, ensure_dict_fn)
    if args.cmd == "core":
        return _run_core_command(args, root)
    if args.cmd == "metrics":
        return _run_metrics(root)
    if args.cmd == "promotion":
        return _run_promotion(args, root)
    if args.cmd == "profile-recalibrate":
        return _run_profile_recalibration(args, root)
    return 2
