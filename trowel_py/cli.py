"""提供 Trowel 后端服务和 Memory 子命令的命令行入口。"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trowel_py.memory.hooks import HookRegistry


def main() -> None:
    """解析根命令，并启动服务或分发 ``memory`` 子命令。"""
    if len(sys.argv) >= 2 and sys.argv[1] == "memory":
        raise SystemExit(_run_memory_cli(sys.argv[2:]))
    parser = argparse.ArgumentParser(prog="trowel-py", description=__doc__)
    parser.add_argument(
        "--port", type=int, default=8000, help="port to listen on (default 8000)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="host to bind (default 127.0.0.1)"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="don't open a browser window"
    )
    args = parser.parse_args()

    # 服务启动前必须完成迁移，否则新数据库没有业务表。
    import logging

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_dir / "trowel.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    from trowel_py.db.connection import create_db
    from trowel_py.db.migrate import run_migrations

    conn = create_db()
    run_migrations(conn)
    conn.close()

    # uvicorn 会阻塞主线程；若启动失败则取消尚未触发的浏览器定时器。
    timer = None
    if not args.no_open:
        url = f"http://{args.host}:{args.port}"
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.start()

    import os

    # uvicorn 稍后按 factory 创建应用，先用环境变量传递实际监听端口。
    os.environ["TROWEL_SERVER_PORT"] = str(args.port)

    # 延迟导入 uvicorn，避免 `--help` 承担服务运行时的导入成本。
    import uvicorn

    try:
        uvicorn.run(
            "trowel_py.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            log_level="info",
            access_log=False,
        )
    except BaseException:
        if timer is not None:
            timer.cancel()
        raise


def _current_iso_week() -> str:
    """返回本地今天所属的 ISO 周，格式为 ``YYYY-Www``。"""
    from trowel_py.memory.cli.commands import current_iso_week

    return current_iso_week()


def _current_month() -> str:
    """返回本地今天所属的月份，格式为 ``YYYY-MM``。"""
    from trowel_py.memory.cli.commands import current_month

    return current_month()


def _ensure_dict_after_batch(root: Path) -> None:
    """批量修改 Note 后检查 Dictionary 一致性，必要时重建。

    Args:
        root: 要检查的 Memory 根目录。
    """
    from trowel_py.memory.cli.maintenance import ensure_dict_after_batch

    ensure_dict_after_batch(root)


def _run_memory_cli(argv: list[str]) -> int:
    """把 ``memory`` 参数交给子命令分发器。

    本模块的日期、维护和修复函数会作为可替换依赖一并传入。

    Args:
        argv: ``memory`` 之后的命令行参数。

    Returns:
        具体子命令的退出码。
    """
    from trowel_py.memory.cli.dispatch import run_memory_cli

    return run_memory_cli(
        argv,
        current_iso_week_fn=_current_iso_week,
        current_month_fn=_current_month,
        ensure_dict_fn=_ensure_dict_after_batch,
        run_review_fn=_run_memory_review,
        run_tidy_fn=_run_memory_tidy,
        run_repair_fn=_run_repair,
        run_backfill_fn=_run_backfill_completed,
    )


def _run_memory_review(registry: HookRegistry, root: Path, date_str: str) -> int:
    """为指定工作日期登记并同步执行 Memory daily review。

    Args:
        registry: 用于登记任务和记录本次派发的 hook registry。
        root: 写入任务使用的 Memory 根目录。
        date_str: 本次 review 的工作目录和备用日期标签，格式为
            ``YYYY-MM-DD``；不限制会话登记日期。

    Returns:
        派发完成或因锁冲突跳过时返回 0。
    """
    from trowel_py.memory.cli.maintenance import run_memory_review

    return run_memory_review(registry, root, date_str)


def _run_memory_tidy(registry: HookRegistry, root: Path) -> int:
    """执行 hook registry 中已登记的 Memory 整理任务。

    Args:
        registry: 保存整理任务和派发记录的 hook registry。
        root: 本次任务使用的 Memory 根目录。

    Returns:
        全部已登记任务处理完成后返回 0；没有任务时也返回 0。
    """
    from trowel_py.memory.cli.maintenance import run_memory_tidy

    return run_memory_tidy(registry, root)


def _run_repair(root: Path, date_str: str, *, apply: bool) -> int:
    """用仍存在的 draft 预览或重建指定日期的 Episode 和 daily。

    dry-run 不写 Episode、daily 或备份，但扫描仍可能创建或迁移 sessions
    数据库。

    Args:
        root: 要扫描或修复的 Memory 根目录。
        date_str: 用于查询 sessions 登记和定位 review 工作目录的日期，格式为
            ``YYYY-MM-DD``。
        apply: False 时只扫描并输出计划；True 时备份 Memory 根目录，再写入
            Episode 并重建 daily。

    Returns:
        报告输出后返回 0；apply 后写入的 Episode 数与可用 draft 数不符时
        返回 1。
    """
    from trowel_py.memory.cli.maintenance import run_repair

    return run_repair(root, date_str, apply=apply)


def _run_backfill_completed(root: Path, date_str: str, *, apply: bool) -> int:
    """预览或回填指定登记日期旧会话的完成水位。

    只处理尚无完成水位的会话；旧任务已提炼整个会话时，执行回填还会同步
    推进提炼水位。

    Args:
        root: 会话数据库所在的 Memory 根目录。
        date_str: 要处理的会话登记日期，格式为 ``YYYY-MM-DD``。
        apply: False 时不更新水位，只输出计划；True 时重新读取各 JSONL 的
            当前字节数并写入水位。

    Returns:
        计划或执行结果输出后返回 0；review 持有独占锁时跳过并返回 0。
    """
    from trowel_py.memory.cli.maintenance import run_backfill_completed

    return run_backfill_completed(root, date_str, apply=apply)


if __name__ == "__main__":
    main()
