"""为质量 runner 自测提供可观察的进程、失败和产物行为。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _run_dir() -> Path:
    """读取自测为当前场景分配的隔离运行目录。"""
    value = os.environ.get("QUALITY_FIXTURE_RUN_DIR")
    if not value:
        raise RuntimeError("missing QUALITY_FIXTURE_RUN_DIR")
    return Path(value)


def _record(task_id: str, run_dir: Path) -> None:
    """记录一个叶子进程确实被 moon 启动。"""
    record = run_dir / "executed.txt"
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open("a", encoding="utf-8") as output:
        output.write(f"{task_id}\n")


def _artifact(run_dir: Path) -> Path:
    """返回 build 与 contracts 共享的同路径产物。"""
    return run_dir / "artifacts" / "index.html"


def _build_parser() -> argparse.ArgumentParser:
    """创建自测叶子进程参数解析器。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--contracts", action="store_true")
    parser.add_argument("--fail", type=int)
    return parser


def main() -> int:
    """执行一次受控通过、失败、构建或契约检查。"""
    args = _build_parser().parse_args()
    run_dir = _run_dir()
    run_id = os.environ.get("QUALITY_FIXTURE_RUN_ID")
    if not run_id:
        raise RuntimeError("missing QUALITY_FIXTURE_RUN_ID")
    _record(args.id, run_dir)
    if args.build:
        if os.environ.get("QUALITY_FIXTURE_FAIL_BUILD") == "1":
            print("intentional build failure")
            return 9
        artifact = _artifact(run_dir)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"run_id={run_id}\n", encoding="utf-8")
    if args.contracts:
        artifact = _artifact(run_dir)
        expected = f"run_id={run_id}\n"
        if not artifact.is_file() or artifact.read_text(encoding="utf-8") != expected:
            print("missing or stale artifact")
            return 8
    if args.fail is not None:
        print(f"intentional failure: {args.fail}")
        return args.fail
    print(f"passed {args.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
