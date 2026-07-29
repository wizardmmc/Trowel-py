"""执行 Memory CLI 的领域命令，并提供当前周、月的周期标识。"""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from collections.abc import Callable
from datetime import date
from pathlib import Path


def current_iso_week() -> str:
    """返回本地今天所属的 ISO 周，格式为 ``YYYY-Www``。"""
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


def current_month() -> str:
    """返回本地今天所属的月份，格式为 ``YYYY-MM``。"""
    return date.today().strftime("%Y-%m")


def _run_dictionary_command(
    args: Namespace,
    root: Path,
    ensure_dict_fn: Callable[[Path], None],
) -> int:
    """执行 Dictionary 重建、只读检查或旧 Note 迁移命令。

    只有 ``migrate --apply`` 成功后才调用字典收敛函数。

    Args:
        args: 已解析的命令参数。``dict-rebuild`` 和 ``migrate`` 读取
            ``apply``，默认只预览，传入 ``--apply`` 才写入；``dict-check``
            不读取额外参数。
        root: 本次命令读写的 Memory 根目录。
        ensure_dict_fn: 迁移写入后检查并收敛 Dictionary 的函数。

    Returns:
        命令处理并输出报告后返回 0。
    """
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
    """提名 Core 候选，或推进候选的批准、激活状态。

    Args:
        args: 已解析的 ``core`` 参数。``nominate`` 读取 Note 文件名去掉
            ``.md`` 后的 ``note_stem``；``approve`` 读取 ``candidate_id``；
            ``activate`` 读取 ``memory_id``。
        root: 本次命令读写的 Memory 根目录。

    Returns:
        命令处理完成时返回 0。
    """
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
    """计算并输出 Memory 健康指标和使用质量指标。

    Args:
        root: 要读取的 Memory 根目录。

    Returns:
        指标成功输出时返回 0。
    """
    from trowel_py.memory.north_star import compute_north_star, memory_usage_metrics

    report = {
        "north_star": compute_north_star(root),
        "usage": memory_usage_metrics(root),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _run_promotion(args: Namespace, root: Path) -> int:
    """按指定策略评估 Core 晋升候选，并输出差距报告。

    默认只读评估；传入 ``--apply`` 时写入或刷新候选文件。

    Args:
        args: 已解析的策略文件路径和 ``--apply`` 开关。
        root: 本次评估使用的 Memory 根目录。

    Returns:
        报告成功输出时返回 0。
    """
    from trowel_py.memory.promotion import evaluate_promotion
    from trowel_py.memory.promotion_policy import PromotionPolicy, load_policy

    policy = load_policy(args.policy) if args.policy else PromotionPolicy()
    report = evaluate_promotion(root, policy, dry_run=not args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def _run_profile_recalibration(args: Namespace, root: Path) -> int:
    """只读计划或隔离运行一次 Profile 历史重校准。

    Args:
        args: 已解析的重校准参数。范围由 ``all`` 或 ``from_date`` 二选一；
            ``run`` 为假时只生成计划，为真时还需提供 ``proxy_base_url``。
        root: 提供历史会话并保存隔离产物的 Memory 根目录。

    Returns:
        计划或重放报告输出后返回 0；范围无效或运行时缺少代理地址时返回 2。
    """
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
        # 使用代理时，CCHost 需从 Claude settings 读取并透传 provider 环境变量。
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


def _run_regeneration(args: Namespace, root: Path) -> int:
    """计划、隔离运行或显式发布一次日周月派生物重生成。

    Args:
        args: 已解析的重生成参数。``run`` 接收 plan ID，``apply`` 接收
            run ID；两者均未提供时，由 ``layer``、``from_period``、
            ``to_period`` 和 ``mode`` 构成新计划。
        root: 本次重生成使用的 Memory 根目录。

    Returns:
        报告输出后返回 0；新建计划缺少必需参数时返回 2。
    """
    from trowel_py.memory.regeneration import (
        apply_regeneration,
        plan_regeneration,
        run_regeneration,
    )

    if args.run:
        run_report = run_regeneration(root, args.run)
        print(json.dumps(run_report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.apply:
        apply_report = apply_regeneration(root, args.apply)
        print(json.dumps(apply_report, ensure_ascii=False, indent=2))
        return 0
    required = {
        "--layer": args.layer,
        "--from": args.from_period,
        "--to": args.to_period,
        "--mode": args.mode,
    }
    missing = [flag for flag, value in required.items() if not value]
    if missing:
        print(f"[memory] regenerate plan needs {', '.join(missing)}")
        return 2
    plan = plan_regeneration(
        root,
        layer=args.layer,
        from_period=args.from_period,
        to_period=args.to_period,
        mode=args.mode,
    )
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


def run_domain_command(
    args: Namespace,
    root: Path,
    *,
    ensure_dict_fn: Callable[[Path], None],
) -> int:
    """分发 Dictionary、Core、指标、晋升、Profile 重校准或重生成命令。

    Args:
        args: 根 Memory 解析器生成的命令参数。
        root: 本次命令使用的 Memory 根目录。
        ensure_dict_fn: Note 迁移写入后收敛 Dictionary 的函数。

    Returns:
        具体命令的返回码；无法识别的命令返回 2。
    """
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
    if args.cmd == "regenerate":
        return _run_regeneration(args, root)
    return 2
