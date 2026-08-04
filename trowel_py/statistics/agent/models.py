"""定义 Agent 统计 adapter 共用的只读来源和统一观察结果。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

Quality = Literal["reliable", "partial", "unavailable"]
SessionStatus = Literal[
    "completed",
    "running",
    "interrupted",
    "failed",
    "unknown",
]
RuntimeName = Literal["claude_code", "codex"]

TOKEN_FIELDS = (
    "input",
    "output",
    "cache_read",
    "cache_creation",
    "reasoning",
    "unknown",
    "total",
)


@dataclass(frozen=True)
class TokenUsage:
    """保存一个时间窗内按原生事实归一后的 token 数量。

    Attributes:
        input: runtime 报告的输入 token；来源未提供时为 None。
        output: runtime 报告的输出 token；来源未提供时为 None。
        cache_read: runtime 报告的缓存输入 token；来源未提供时为 None。
        cache_creation: Claude Code 报告的缓存创建 token；其他来源为 None。
        reasoning: Codex 报告的推理输出 token；其他来源为 None。
        unknown: 原生总量中无法归类的 token；无法计算时为 None。
        total: 包含缓存输入的总 token；无法可靠计算时为 None。
    """

    input: int | None = None
    output: int | None = None
    cache_read: int | None = None
    cache_creation: int | None = None
    reasoning: int | None = None
    unknown: int | None = None
    total: int | None = None


@dataclass(frozen=True)
class ActivityInterval:
    """表示查询时间窗内一段可观测的 Agent 活动。

    Attributes:
        start: 活动开始时间。
        end: 活动结束时间。
        quality: 终点是否来自可靠 terminal。
    """

    start: datetime
    end: datetime
    quality: Quality


@dataclass(frozen=True)
class ModelObservation:
    """保存一个 session 中可归因到单个模型的使用事实。

    Attributes:
        model: runtime 实际回报的模型；没有事实时为 None。
        tokens: 只归属于该模型的 token 增量。
        response_samples: 首段可见响应由该模型产生的毫秒样本。
    """

    model: str | None
    tokens: TokenUsage
    response_samples: tuple[int, ...]


@dataclass(frozen=True)
class ClaudeBindingSource:
    """描述一个 Trowel 会话对应的 Claude Code transcript 区间。

    Attributes:
        session_id: Trowel 会话 ID。
        native_session_id: Claude Code 原生会话 ID，仅用于读取来源。
        transcript_path: 原生 transcript 文件路径。
        start_offset: 当前 binding 的起始字节；旧记录缺失时为 None。
        end_offset: 当前 binding 的结束字节；无法确定时使用已存在文件末尾。
        bound_at: 当前 binding 建立时间。
        completed_at: 当前 binding 的可靠终态时间；运行中或旧记录为 None。
        status: 当前 binding 的最新状态。
        model: binding 中 runtime 已回报的模型；没有事实时为 None。
    """

    session_id: str
    native_session_id: str
    transcript_path: Path
    start_offset: int | None
    end_offset: int
    bound_at: str
    completed_at: str | None
    status: SessionStatus
    model: str | None


@dataclass(frozen=True)
class CodexTurnSource:
    """描述 sessions registry 中一个 Codex normalized turn。

    Attributes:
        session_id: 首次登记该 turn 的 Trowel 会话 ID。
        native_session_id: Codex thread ID，仅用于来源关联。
        turn_id: Codex 原生 turn ID。
        journal_path: Trowel normalized journal 路径。
        registered_at: turn 首次登记时间。
        completed_at: journal 封口后的终态时间；运行中为 None。
        status: registry 保存的 turn 状态。
        model: turn 开始时 runtime 回报的模型；未知时为空字符串。
    """

    session_id: str
    native_session_id: str
    turn_id: str
    journal_path: Path
    registered_at: str
    completed_at: str | None
    status: SessionStatus
    model: str


@dataclass(frozen=True)
class SessionObservation:
    """保存一个 Trowel session 在查询窗内的统一统计事实。

    Attributes:
        session_id: Trowel 会话 ID。
        runtime: 产生该会话的运行工具。
        models: 查询窗内由 binding 或事件确认的模型名。
        started_at: 当前 session 首个可观察活动的时间。
        status: 查询窗结束时可确认的 session 状态。
        tokens: 查询窗内 token 增量。
        response_samples: 用户提交到首段可见文字的毫秒样本。
        intervals: 已裁剪到查询窗内的活动区间。
        quality: 当前 session 来源的最低数据质量。
        model_observations: 只供 runtime/model 汇总使用的模型事实切片。
    """

    session_id: str
    runtime: RuntimeName
    models: tuple[str, ...]
    started_at: datetime
    status: SessionStatus
    tokens: TokenUsage
    response_samples: tuple[int, ...]
    intervals: tuple[ActivityInterval, ...]
    quality: Quality
    model_observations: tuple[ModelObservation, ...] = ()


def combine_token_usage(values: list[TokenUsage]) -> TokenUsage:
    """合并已知 token；缺失样本不清空同组已经观测到的数量。

    Args:
        values: 同一统计分组中的 token 增量。

    Returns:
        各分类的已知小计；空列表或全部样本都缺该字段时为 None。
    """

    if not values:
        return TokenUsage()
    totals: dict[str, int | None] = {}
    for field in TOKEN_FIELDS:
        parts = [getattr(value, field) for value in values]
        known = [part for part in parts if part is not None]
        totals[field] = sum(known) if known else None
    return TokenUsage(**totals)


def subtract_token_usage(end: TokenUsage, start: TokenUsage) -> TokenUsage:
    """按分类计算两个累计 token 水位的非负差。

    Args:
        end: 区间结束累计水位。
        start: 区间开始累计水位。

    Returns:
        两端都存在且未倒退的分类差值，否则该分类为 None。
    """

    values: dict[str, int | None] = {}
    for field in TOKEN_FIELDS:
        end_value = getattr(end, field)
        start_value = getattr(start, field)
        values[field] = (
            end_value - start_value
            if end_value is not None
            and start_value is not None
            and end_value >= start_value
            else None
        )
    return TokenUsage(**values)


def quality_worst(*values: Quality) -> Quality:
    """返回一组质量等级中最保守的等级。"""

    rank = {"reliable": 0, "partial": 1, "unavailable": 2}
    return max(values, key=rank.__getitem__) if values else "unavailable"


def combine_model_observations(
    values: list[ModelObservation],
) -> tuple[ModelObservation, ...]:
    """按模型合并 token 和首响样本，不跨模型重复计算。

    Args:
        values: 同一 session 或统计分组中的模型事实切片。

    Returns:
        按未知模型在前、模型名升序排列的合并结果。
    """

    grouped: dict[str | None, list[ModelObservation]] = {}
    for value in values:
        grouped.setdefault(value.model, []).append(value)
    return tuple(
        ModelObservation(
            model=model,
            tokens=combine_token_usage(
                [
                    value.tokens
                    for value in observations
                    if any(
                        getattr(value.tokens, field) is not None
                        for field in TOKEN_FIELDS
                    )
                ]
            ),
            response_samples=tuple(
                sample
                for value in observations
                for sample in value.response_samples
            ),
        )
        for model, observations in sorted(
            grouped.items(),
            key=lambda item: (item[0] is not None, item[0] or ""),
        )
    )
