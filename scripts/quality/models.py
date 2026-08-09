"""定义质量叶子的四态结果和稳定机器摘要。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class QualityStatus(StrEnum):
    """表示一个稳定质量叶子的最终状态。"""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class QualityResult:
    """记录一个稳定叶子的状态、原因和证据入口。

    Attributes:
        id: 本地、CI、文档和摘要共同引用的稳定叶子 ID。
        target: moon 内部用于定位项目和任务的完整 target。
        status: 通过、失败、依赖阻断或永久平台不适用状态。
        duration_seconds: moon 为该 action 记录的耗时秒数。
        exit_code: 已启动进程的退出码；未启动时为 None。
        reason: 失败、阻断或不适用的简短机器原因。
        hint: manifest 中面向维护者的排查入口。
        evidence: 相对本次运行目录的叶子证据目录。
    """

    id: str
    target: str
    status: QualityStatus
    duration_seconds: float
    exit_code: int | None
    reason: str | None
    hint: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        """转换成可直接写入 JSON 的稳定字段。"""
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True)
class RunSummary:
    """汇总一次 profile 或单叶调用的全部适用结果。

    Attributes:
        requested_target: 调用方传入的 profile 或稳定叶子 ID。
        moon_exit_code: moon 原始进程退出码，用于发现适配器未解释的框架失败。
        results: 按稳定叶子 ID 排序的结果。
        runner_error: moon 非零退出但没有叶子失败可解释时的框架错误。
    """

    requested_target: str
    moon_exit_code: int
    results: tuple[QualityResult, ...]
    runner_error: str | None = None

    @property
    def exit_code(self) -> int:
        """任一失败、阻断或未解释的 moon 失败都返回 1。"""
        has_required_failure = any(
            result.status in {QualityStatus.FAILED, QualityStatus.BLOCKED}
            for result in self.results
        )
        return 1 if has_required_failure or self.runner_error else 0

    def to_dict(self) -> dict[str, Any]:
        """转换成机器摘要使用的稳定 JSON 结构。"""
        return {
            "requested_target": self.requested_target,
            "moon_exit_code": self.moon_exit_code,
            "exit_code": self.exit_code,
            "runner_error": self.runner_error,
            "results": [result.to_dict() for result in self.results],
        }
