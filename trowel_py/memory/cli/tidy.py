"""Memory tidy 子命令。"""

from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path


def run_tidy_command(
    args: Namespace,
    root: Path,
    *,
    current_iso_week_fn: Callable[[], str],
    current_month_fn: Callable[[], str],
    ensure_dict_fn: Callable[[Path], None],
    run_tidy_fn: Callable[[object, Path], int],
) -> int:
    """执行 tidy 分支。"""
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
