"""Memory CLI 分发边界。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from .commands import run_domain_command
from .parser import build_parser
from .tidy import run_tidy_command


def run_memory_cli(
    argv: list[str],
    *,
    current_iso_week_fn: Callable[[], str],
    current_month_fn: Callable[[], str],
    ensure_dict_fn: Callable[[Path], None],
    run_review_fn: Callable[[object, Path, str], int],
    run_tidy_fn: Callable[[object, Path], int],
    run_repair_fn: Callable[..., int],
    run_backfill_fn: Callable[..., int],
) -> int:
    """解析并分发 `trowel-py memory`。"""
    args = build_parser().parse_args(argv)

    from trowel_py.memory import hooks, paths

    root = Path(args.root) if args.root else paths.resolve_memory_root()
    if args.cmd == "tidy":
        return run_tidy_command(
            args,
            root,
            current_iso_week_fn=current_iso_week_fn,
            current_month_fn=current_month_fn,
            ensure_dict_fn=ensure_dict_fn,
            run_tidy_fn=run_tidy_fn,
        )
    if args.cmd == "review":
        return run_review_fn(hooks.default, root, args.date or date.today().isoformat())
    if args.cmd == "repair":
        return run_repair_fn(root, args.date, apply=args.apply)
    if args.cmd == "backfill-completed":
        return run_backfill_fn(root, args.date, apply=args.apply)
    return run_domain_command(args, root, ensure_dict_fn=ensure_dict_fn)
