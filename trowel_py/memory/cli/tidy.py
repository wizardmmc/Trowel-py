"""执行 Memory tidy 的回滚、状态查询、补跑、周月任务和已登记任务分发。"""

from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path

from trowel_py.memory.hooks import HookRegistry


def run_tidy_command(
    args: Namespace,
    root: Path,
    *,
    current_iso_week_fn: Callable[[], str],
    current_month_fn: Callable[[], str],
    ensure_dict_fn: Callable[[Path], None],
    run_tidy_fn: Callable[[HookRegistry, Path], int],
) -> int:
    """根据已解析参数选择并执行一项 Memory tidy 操作。

    同时启用多个操作时，依次优先选择回滚、状态查询、显式补跑、周任务和月
    任务；以上操作均未启用时，使用默认 hook registry 分发其中已登记的全部
    tidy job。

    Args:
        args: 已解析的 tidy 操作、周期和补跑范围参数。
        root: 本次操作使用的 Memory 根目录。
        current_iso_week_fn: 未指定周时返回当前 ISO 周标识（``YYYY-Www``）的函数。
        current_month_fn: 未指定月时返回当前月份标识（``YYYY-MM``）的函数。
        ensure_dict_fn: 回滚 Note 后检查 Dictionary 一致性，并在不一致时尝试
            重建的函数。
        run_tidy_fn: 使用默认 hook registry 分发其中全部已登记 tidy job 的函数。

    Returns:
        回滚、状态查询、显式补跑或周月任务完成输出后返回 0；显式补跑缺少
        范围参数时返回 2；未选择上述操作时返回 ``run_tidy_fn`` 的返回码。
    """
    if args.rollback:
        from trowel_py.memory.tidy import rollback_plan

        rollback_plan(root, args.rollback)
        print(f"[memory] tidy rollback: plan {args.rollback} restored")
        ensure_dict_fn(root)
        return 0
    if args.status:
        from trowel_py.memory.tidy_state import tidy_status

        print(json.dumps(tidy_status(root), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.catchup:
        if not args.from_period or not args.scope:
            print(
                "[memory] tidy --catchup needs --from PERIOD and --scope weekly|monthly"
            )
            return 2
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider
        from trowel_py.memory.tidy_scheduler import run_explicit_catchup

        def provider_factory() -> AnthropicProvider:
            """为每个实际补跑的周期按需创建 LLM provider。"""
            return AnthropicProvider(load_llm_config())

        result = run_explicit_catchup(
            root,
            args.scope,
            args.from_period,
            provider_factory,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.weekly:
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider
        from trowel_py.memory.tidy import run_weekly_tidy

        iso_week = args.iso_week or current_iso_week_fn()
        report = run_weekly_tidy(
            root,
            iso_week,
            AnthropicProvider(load_llm_config()),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.monthly:
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider
        from trowel_py.memory.tidy import run_monthly_tidy

        month = args.month or current_month_fn()
        report = run_monthly_tidy(
            root,
            month,
            AnthropicProvider(load_llm_config()),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    from trowel_py.memory import hooks

    return run_tidy_fn(hooks.default, root)
