"""定义 Agent Statistics API 的公开只读响应。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from trowel_py.statistics.schemas import SourceFreshness

Quality = Literal["reliable", "partial", "unavailable"]
SessionStatus = Literal["completed", "running", "interrupted", "failed", "unknown"]


class AgentTokenUsageData(BaseModel):
    """公开分类 token 增量及其质量。

    Attributes:
        input: 输入 token；来源未提供时为 None。
        output: 输出 token；来源未提供时为 None。
        cache_read: 缓存输入 token；来源未提供时为 None。
        cache_creation: 缓存创建 token；来源未提供时为 None。
        reasoning: 推理输出 token；来源未提供时为 None。
        unknown: 无法归类的原生 token；无法计算时为 None。
        total: 当前时间窗总 token；无法可靠计算时为 None。
        total_includes_cache_input: total 是否包含 cache_read。
        quality: token 来源的最低质量。
    """

    input: int | None
    output: int | None
    cache_read: int | None
    cache_creation: int | None
    reasoning: int | None
    unknown: int | None
    total: int | None
    total_includes_cache_input: bool = True
    quality: Quality


class AgentLatencyDistributionData(BaseModel):
    """公开首次可见响应分布和样本门槛结果。

    Attributes:
        sample_size: 用户提交到首段可见文字的有效样本数。
        p50_ms: 至少 5 个样本时的中位数，否则为 None。
        p95_ms: 至少 20 个样本时的 p95，否则为 None。
        p99_ms: 至少 100 个样本时的 p99，否则为 None。
        quality: 有 p50 时继承来源质量，否则为 unavailable。
    """

    sample_size: int
    p50_ms: int | None
    p95_ms: int | None
    p99_ms: int | None
    quality: Quality


class AgentStatusCountsData(BaseModel):
    """公开 Trowel sessions 的确定性状态数量。

    Attributes:
        completed: 最后终态为正常完成的 session 数。
        running: 查询窗结束时仍在运行的 session 数。
        interrupted: 最后终态为用户中断的 session 数。
        failed: 最后终态为执行失败的 session 数。
        unknown: 旧记录或来源不足而无法确认终态的 session 数。
    """

    completed: int
    running: int
    interrupted: int
    failed: int
    unknown: int


class AgentActivityData(BaseModel):
    """公开 session 活动时长之和与跨 session 并集。

    Attributes:
        session_sum_ms: 各 session 内合并活动区间后的毫秒数之和。
        concurrent_union_ms: 所有 session 区间合并后的毫秒数，不重复计算并发。
        quality: 所有活动区间中最低的终点质量。
    """

    session_sum_ms: int
    concurrent_union_ms: int
    quality: Quality


class AgentModelSummaryData(BaseModel):
    """公开一个 runtime/model 分组的 session 使用情况。

    Attributes:
        runtime: claude_code 或 codex。
        model: runtime 实际回报的模型；未知时为 None。
        session_count: 查询窗内使用过该模型的 Trowel session 数。
        statuses: 这些 session 的状态分组。
        tokens: 只归属于该模型的 token 增量。
        first_visible_response: 首段文字由该模型产生的延迟分布。
        cache_input_ratio: cache_read/input 的比例；字段缺失或 input 为 0 时为 None。
        activity_ms: 使用过该模型的 session 活动时长之和。
        quality: 分组来源的最低质量。
    """

    runtime: Literal["claude_code", "codex"]
    model: str | None
    session_count: int
    statuses: AgentStatusCountsData
    tokens: AgentTokenUsageData
    first_visible_response: AgentLatencyDistributionData
    cache_input_ratio: float | None
    activity_ms: int
    quality: Quality


class AgentSessionData(BaseModel):
    """公开最近一个 Trowel session 的统计行。

    Attributes:
        session_id: Trowel 分配的会话 ID，不是原生 runtime ID。
        runtime: claude_code 或 codex。
        models: 当前时间窗内 runtime 实际回报的模型集合。
        started_at: 当前 session 首个可观察活动时间。
        activity_ms: 当前 session 内合并活动区间后的毫秒数。
        first_visible_response: 当前 session 内的首响分布。
        tokens: 当前 session 在查询窗内的 token 增量。
        status: 查询窗结束时可确认的状态。
        quality: 当前 session 来源的最低质量。
    """

    session_id: str
    runtime: Literal["claude_code", "codex"]
    models: tuple[str, ...]
    started_at: datetime
    activity_ms: int
    first_visible_response: AgentLatencyDistributionData
    tokens: AgentTokenUsageData
    status: SessionStatus
    quality: Quality


class AgentStatisticsData(BaseModel):
    """公开 Agent 页所需的时间窗、总览、模型和 session 明细。

    Attributes:
        generated_at: 本次 read model 生成时间。
        window_start: 查询包含的开始时刻。
        window_end: 查询不包含的结束时刻。
        timezone: 调用方选择的 IANA 时区。
        sample_size: 查询窗内 Trowel user session 数。
        quality: 全部 session 来源的最低质量。
        freshness: Agent 事实源最后更新时间。
        statuses: session 状态分组。
        tokens: 查询窗内全部 token 增量。
        first_visible_response: 查询窗内全部首次可见响应分布。
        activity: 活动时长之和与跨 session 并集。
        model_summaries: 按 runtime 和真实模型分组的结果。
        sessions: 按开始时间倒序排列的最近 session，最多 100 条。
    """

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    timezone: str
    sample_size: int
    quality: Quality
    freshness: dict[str, SourceFreshness]
    statuses: AgentStatusCountsData
    tokens: AgentTokenUsageData
    first_visible_response: AgentLatencyDistributionData
    activity: AgentActivityData
    model_summaries: list[AgentModelSummaryData]
    sessions: list[AgentSessionData]
