"""用组合协调 moon gateway、四态转换、排他锁和证据存储。"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scripts.quality.evidence import EvidenceStore
from scripts.quality.locking import QualityRunLock
from scripts.quality.models import RunSummary
from scripts.quality.moon import RawMoonRun
from scripts.quality.report import normalize_moon_run


class QualityGateway(Protocol):
    """定义 service 执行一个 moon 目标所需的最小上游接口。"""

    def execute(
        self,
        requested_target: str,
        *,
        keep_going: bool,
        ci: bool,
        console_path: Path,
    ) -> RawMoonRun:
        """执行目标并返回未解释的 moon 事实。

        Args:
            requested_target: profile 或稳定叶子 ID。
            keep_going: 是否在独立分支失败后继续执行。
            ci: 是否启用 CI 严格行为。
            console_path: 保存 moon 完整控制台输出的路径。

        Returns:
            同一次 moon 调用产生的报告、任务图、定义和状态目录。
        """
        ...


class QualityReportError(RuntimeError):
    """表示 moon 报告可解析但不符合固定版本的字段契约。"""


@dataclass(frozen=True)
class QualityRunRequest:
    """描述一次 profile 或单叶质量调用。

    Attributes:
        target: ``gate``、``gate-full`` 或稳定叶子 ID。
        run_dir: 保存本次完整证据的独占目录。
        ci: 是否启用 CI 严格上下文和 moon CI 行为。
        fail_fast: 是否在首个失败后停止，仅供本地定位使用。
    """

    target: str
    run_dir: Path
    ci: bool = False
    fail_fast: bool = False


def current_moon_os() -> str:
    """返回 moon ``options.os`` 使用的平台名。"""
    systems = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}
    try:
        return systems[platform.system()]
    except KeyError as error:
        raise RuntimeError(
            f"unsupported quality platform: {platform.system()}"
        ) from error


class QualityRunService:
    """执行一次质量目标，并在释放锁前完成四态解释和证据复制。"""

    def __init__(
        self,
        *,
        gateway: QualityGateway,
        workspace_root: Path,
        current_os: str | None = None,
    ) -> None:
        """注入上游 gateway、workspace 和可测试的平台事实。

        Args:
            gateway: 只负责返回原始 moon 事实的最小接口。
            workspace_root: moon workspace 根，用于建立并发运行锁。
            current_os: moon 平台名；不传时读取当前系统。
        """
        self._gateway = gateway
        self._workspace_root = workspace_root
        self._current_os = current_os or current_moon_os()

    def run(self, request: QualityRunRequest) -> RunSummary:
        """执行请求并返回已经持久化证据的稳定摘要。

        Args:
            request: 目标、证据目录、CI 模式和 fail-fast 选择。

        Returns:
            按稳定 ID 排序的四态摘要。
        """
        lock_path = self._workspace_root / ".moon" / "quality-run.lock"
        with QualityRunLock(lock_path):
            if request.run_dir.exists():
                if not request.run_dir.is_dir():
                    raise FileExistsError(
                        f"quality evidence path is not a directory: {request.run_dir}"
                    )
                if any(request.run_dir.iterdir()):
                    raise FileExistsError(
                        f"quality evidence directory is not empty: {request.run_dir}"
                    )
            raw_run = self._gateway.execute(
                request.target,
                keep_going=not request.fail_fast,
                ci=request.ci,
                console_path=request.run_dir / "console.log",
            )
            evidence = EvidenceStore(request.run_dir)
            evidence.persist_raw(raw_run)
            try:
                summary = normalize_moon_run(
                    raw_run.report,
                    raw_run.graph,
                    raw_run.definitions,
                    current_os=self._current_os,
                    requested_target=request.target,
                    moon_exit_code=raw_run.exit_code,
                    fail_fast=request.fail_fast,
                )
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise QualityReportError(
                    "moon 2.4.6 report does not match the expected action schema"
                ) from error
            evidence.persist_summary(raw_run, summary)
        return summary
