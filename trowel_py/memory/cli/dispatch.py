"""解析 ``trowel-py memory`` 参数并将子命令分发给对应处理器。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from trowel_py.memory.hooks import HookRegistry

from .commands import run_domain_command
from .parser import build_parser
from .tidy import run_tidy_command


def run_memory_cli(
    argv: list[str],
    *,
    current_iso_week_fn: Callable[[], str],
    current_month_fn: Callable[[], str],
    ensure_dict_fn: Callable[[Path], None],
    run_review_fn: Callable[[HookRegistry, Path, str], int],
    run_tidy_fn: Callable[[HookRegistry, Path], int],
    run_repair_fn: Callable[..., int],
    run_backfill_fn: Callable[..., int],
) -> int:
    """解析 Memory CLI 参数并分发子命令。

    未传 ``--root`` 时从项目配置解析 Memory 根目录；``review`` 未传日期时
    使用本地今天。签名中的依赖函数由根 CLI 模块传入，调用方可以替换默认
    实现。

    Args:
        argv: ``memory`` 之后的命令行参数。
        current_iso_week_fn: 返回当前 ISO 周标识（``YYYY-Www``）的函数，供
            tidy 填充默认周期。
        current_month_fn: 返回当前月份标识（``YYYY-MM``）的函数，供 tidy
            填充默认周期。
        ensure_dict_fn: 批量修改 Note 后检查 Dictionary 一致性，并在不一致
            时尝试重建的函数。
        run_review_fn: 执行单日 Memory 写入任务的函数。
        run_tidy_fn: 执行已注册 tidy job 的函数。
        run_repair_fn: 根据仍存在的 draft 重建指定日期内各会话 episode 的函数。
        run_backfill_fn: 按 JSONL 当前字节数回填指定日期旧会话完成水位的函数。

    Returns:
        被分发处理器的返回码。
    """
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
