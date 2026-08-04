"""定义 Statistics 总览公开的汇总、趋势、状态和来源 DTO。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from trowel_py.statistics.agent.schemas import (
    AgentActivityData,
    AgentLatencyDistributionData,
    AgentStatusCountsData,
    AgentTokenUsageData,
)
from trowel_py.statistics.memory.schemas import MemoryRatioData
from trowel_py.statistics.runtime.schemas import DatabaseFileStatistics
from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.session_problems.schemas import SessionProblemItemData

OverviewQuality = Literal["reliable", "partial", "unavailable"]


class OverviewAgentData(BaseModel):
    """公开当前选择时间窗的 Agent 关键事实。

    Attributes:
        user_sessions: 与查询窗相交的 Trowel 用户 session 数。
        statuses: 用户 session 的确定性终态数量。
        tokens: 按 runtime 水位归一后的分类 token 增量。
        first_visible_response: 用户提交到首段可见文字的延迟分布。
        activity: session 活动时长之和与并发区间并集。
        quality: Agent 来源在当前时间窗的整体质量。
    """

    user_sessions: int
    statuses: AgentStatusCountsData
    tokens: AgentTokenUsageData
    first_visible_response: AgentLatencyDistributionData
    activity: AgentActivityData
    quality: OverviewQuality


class OverviewTokenTrendPointData(BaseModel):
    """公开所选时间范围趋势中的一个当地日期。

    Attributes:
        date: 该点代表的调用方当地日期。
        session_count: 当天可归因的 Trowel 用户 session 数。
        known_token_session_count: 当天 total token 水位可用的 session 数。
        token_total: 当天可归因 token；来源不足时为 None，不用 0 代替。
        quality: 当天 token 水位的证据质量。
    """

    date: date
    session_count: int
    known_token_session_count: int
    token_total: int | None
    quality: OverviewQuality


class OverviewMemoryData(BaseModel):
    """公开 Memory 漏斗、效果和资产摘要。

    Attributes:
        search_hits: 搜索返回的 Note 候选数量。
        reads: 实际打开 Note 正文的次数。
        judged_effects: helpful、harmful 与 unused 的共同效果分母。
        helpful: 判定为 helpful 的 Note-session 对数。
        helpful_rate: helpful 使用自己的效果分母计算的比例。
        judgement_coverage: 已判定用户 session 占可判 session 的比例。
        recall_miss_rate: 两类 recall miss 占已判定 session 的比例。
        attribution_coverage: Memory 访问记录能归到会话的比例。
        active_notes: 当前可检索的 active Note 数。
        quality: Memory 来源在当前时间窗的整体质量。
    """

    search_hits: int
    reads: int
    judged_effects: int
    helpful: int
    helpful_rate: MemoryRatioData
    judgement_coverage: MemoryRatioData
    recall_miss_rate: MemoryRatioData
    attribution_coverage: MemoryRatioData
    active_notes: int
    quality: OverviewQuality


class OverviewStatusData(BaseModel):
    """公开一条由确定性规则生成的状态或采集缺口。

    Attributes:
        code: 前端稳定识别的状态代码，不包含动态身份。
        level: 状态在列表中的严重程度；unavailable 表示证据缺失。
        title: 可直接展示的状态事实。
        detail: 解释事实边界且不做原因猜测的补充文本。
        source: 产生该事实的受控 read model 或事实源名称。
        quality: 支撑该状态的数据质量。
        freshness: 该状态事实最后更新到的时刻。
    """

    code: str
    level: Literal["info", "warning", "error", "unavailable"]
    title: str
    detail: str
    source: str
    quality: OverviewQuality
    freshness: SourceFreshness


class OverviewSessionProblemsData(BaseModel):
    """公开最近会话问题和整个时间窗的处理数量。

    Attributes:
        reviewed_session_count: 时间窗内已经完成问题分析的会话数。
        problem_count: 分析后得到非空问题的会话数。
        quality: 会话问题来源的整体质量。
        freshness: 最近一条完成记录的处理时刻。
        items: 按关闭时间从新到旧排列的可复制问题，最多五条。
    """

    reviewed_session_count: int
    problem_count: int
    quality: OverviewQuality
    freshness: SourceFreshness
    items: list[SessionProblemItemData]


class OverviewSourceData(BaseModel):
    """公开一个下游 read model 的样本和质量摘要。

    Attributes:
        label: 前端展示的稳定领域名称。
        sample_size: 下游 read model 自己定义的样本数量。
        quality: 下游 read model 报告的质量。
        freshness: 下游全部事实源合并后的最近更新时间和状态。
    """

    label: str
    sample_size: int
    quality: OverviewQuality
    freshness: SourceFreshness


class OverviewStatisticsData(BaseModel):
    """汇总五个公开 read model，供 Statistics 总览一次读取。

    Attributes:
        generated_at: 本次总览响应生成时刻。
        window_start: 查询包含的开始时刻。
        window_end: 查询不包含的结束时刻。
        timezone: 调用方选择的 IANA 时区。
        sample_size: 当前有可用样本的下游 read model 数量，范围为 0 至 5。
        quality: 五个下游来源的合并质量；混合质量统一标 partial。
        freshness: 按领域前缀保留的全部下游来源新鲜度。
        agent: 当前选择时间窗的 Agent 摘要。
        token_trend: 当前选择范围内从旧到新的逐日 token 统计。
        memory: 当前选择时间窗的 Memory 摘要。
        statuses: 确定性 session、运行和采集缺口事实。
        session_problems: 当前选择时间窗最近五条会话问题。
        database_files: 三个受控本机数据库及附属文件的当前体积。
        sources: 五个下游 read model 的质量和样本摘要。
    """

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    timezone: str
    sample_size: int
    quality: OverviewQuality
    freshness: dict[str, SourceFreshness]
    agent: OverviewAgentData
    token_trend: list[OverviewTokenTrendPointData]
    memory: OverviewMemoryData
    statuses: list[OverviewStatusData]
    session_problems: OverviewSessionProblemsData
    database_files: list[DatabaseFileStatistics]
    sources: dict[str, OverviewSourceData]
