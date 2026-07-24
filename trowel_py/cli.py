"""启动 Trowel 服务，并分发 memory 子命令。"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path


def main() -> None:
    """启动服务；位置一的 memory 子命令先于服务参数解析。"""
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
    conn.close()  # 释放迁移写锁，后续请求才能写入。

    # uvicorn 会阻塞主线程；若启动失败则取消尚未触发的浏览器定时器。
    timer = None
    if not args.no_open:
        url = f"http://{args.host}:{args.port}"
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.start()

    # 延迟导入避免 `--help` 承担 uvicorn 启动成本。
    import os

    # factory 模式下应用尚未创建，只能通过环境变量向 lifespan 传递端口。
    os.environ["TROWEL_SERVER_PORT"] = str(args.port)

    import uvicorn

    try:
        uvicorn.run(
            "trowel_py.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            log_level="info",
        )
    except BaseException:
        if timer is not None:
            timer.cancel()
        raise


def _current_iso_week() -> str:
    """返回当前 ISO 周。"""
    from trowel_py.memory.cli.commands import current_iso_week

    return current_iso_week()


def _current_month() -> str:
    """返回当前月份。"""
    from trowel_py.memory.cli.commands import current_month

    return current_month()


def _ensure_dict_after_batch(root: Path) -> None:
    """批处理改动 note 后收敛字典。"""
    from trowel_py.memory.cli.maintenance import ensure_dict_after_batch

    ensure_dict_after_batch(root)


def _run_memory_cli(argv: list[str]) -> int:
    """分发 memory 子命令，并保留根模块 patch 边界。"""
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


def _run_memory_review(registry: object, root: Path, date_str: str) -> int:
    """分发单日 memory 写入任务。"""
    from trowel_py.memory.cli.maintenance import run_memory_review

    return run_memory_review(registry, root, date_str)


def _run_memory_tidy(registry: object, root: Path) -> int:
    """分发已注册的 memory 整理任务。"""
    from trowel_py.memory.cli.maintenance import run_memory_tidy

    return run_memory_tidy(registry, root)


def _run_repair(root: Path, date_str: str, *, apply: bool) -> int:
    """修复历史 episode 投影。"""
    from trowel_py.memory.cli.maintenance import run_repair

    return run_repair(root, date_str, apply=apply)


def _run_backfill_completed(root: Path, date_str: str, *, apply: bool) -> int:
    """按 JSONL 当前大小回填历史完成水位。"""
    from trowel_py.memory.cli.maintenance import run_backfill_completed

    return run_backfill_completed(root, date_str, apply=apply)


if __name__ == "__main__":
    main()
