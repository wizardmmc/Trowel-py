"""提供本地 Agent 与 CI 共用的质量 profile 和单叶命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from scripts.quality.installer import MoonInstallError, ensure_moon
from scripts.quality.models import QualityStatus, RunSummary
from scripts.quality.moon import MoonExecutionError, MoonGateway
from scripts.quality.service import QualityRunRequest, QualityRunService

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_run_dir() -> Path:
    """返回带时间和进程号的本次运行证据目录。"""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / ".quality-runs" / f"{timestamp}-{os.getpid()}"


def build_parser() -> argparse.ArgumentParser:
    """创建质量任务图命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.quality",
        description="运行权威质量 profile 或一个稳定叶子。",
    )
    parser.add_argument("target", help="gate、gate-full 或稳定叶子 ID")
    parser.add_argument("--ci", action="store_true", help="启用 CI 严格模式")
    parser.add_argument(
        "--fail-fast", action="store_true", help="首个失败后停止，仅用于定位"
    )
    parser.add_argument("--output", type=Path, help="本次运行证据目录")
    return parser


def _print_summary(summary: RunSummary, summary_path: Path) -> None:
    """打印短而稳定的四态摘要和完整证据入口。

    Args:
        summary: 已归一并持久化的本次运行结果。
        summary_path: 控制台最后显示的机器摘要文件路径。
    """
    for result in summary.results:
        detail = result.reason or result.hint
        print(
            f"{result.id:<32} {result.status.value:<14} "
            f"{result.duration_seconds:>7.3f}s  {detail}"
        )
    if summary.runner_error:
        print(f"quality.runner                   failed         {summary.runner_error}")
    print(f"summary: {summary_path}")


def _write_runner_error(run_dir: Path, error: Exception) -> Path:
    """在 moon 未产生有效报告时仍留下稳定错误证据。

    Args:
        run_dir: 本次失败调用独占的证据目录。
        error: 安装、进程或报告阶段抛出的原始异常。

    Returns:
        写入后的 runner 错误证据路径。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    error_path = run_dir / "runner-error.json"
    error_path.write_text(
        json.dumps(
            {"status": QualityStatus.FAILED.value, "reason": str(error)},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return error_path


def main(argv: Sequence[str] | None = None) -> int:
    """准备 moon、执行目标、保存证据并返回聚合退出码。

    Args:
        argv: 不含程序名的命令行参数；不传时读取当前进程参数。
    """
    args = build_parser().parse_args(argv)
    run_dir = (args.output or _default_run_dir()).resolve()
    try:
        executable = ensure_moon()
        gateway = MoonGateway(executable=executable, workspace_root=REPO_ROOT)
        service = QualityRunService(gateway=gateway, workspace_root=REPO_ROOT)
        summary = service.run(
            QualityRunRequest(
                target=args.target,
                run_dir=run_dir,
                ci=args.ci,
                fail_fast=args.fail_fast,
            )
        )
    except FileExistsError as error:
        print(f"quality runner failed: {error}", file=sys.stderr)
        return 2
    except (MoonInstallError, MoonExecutionError, OSError, RuntimeError) as error:
        evidence = _write_runner_error(run_dir, error)
        print(f"quality runner failed: {error}", file=sys.stderr)
        print(f"evidence: {evidence}", file=sys.stderr)
        return 2
    summary_path = run_dir / "summary.json"
    _print_summary(summary, summary_path)
    return summary.exit_code
