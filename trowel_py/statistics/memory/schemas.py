"""定义 Memory 统计公开响应的分子分母、来源和资产 DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from trowel_py.statistics.schemas import SourceFreshness

Quality = Literal["reliable", "partial", "unavailable"]


class MemoryRatioData(BaseModel):
    """公开一个不能与其他指标混用分母的比例。

    Attributes:
        numerator: 当前指标自己的分子。
        denominator: 当前指标自己的分母；没有样本时为 0。
        ratio: 分子除以分母；没有分母时为 None。
        quality: 当前分子、分母和比例的证据完整度。
    """

    numerator: int
    denominator: int
    ratio: float | None
    quality: Quality


class MemoryAttributionData(BaseModel):
    """公开访问事件的身份归因覆盖情况。

    Attributes:
        attributed: 已归到用户或内部会话的访问记录数。
        unattributed: 无法确认会话归属的访问记录数。
        coverage: 以全部访问记录为分母的归因覆盖率。
        quality: 访问记录身份与时间的证据完整度。
    """

    attributed: int
    unattributed: int
    coverage: MemoryRatioData
    quality: Quality


class MemoryRetrievalData(BaseModel):
    """公开搜索调用、候选命中和实际读取。

    Attributes:
        search_calls: 带查询文本的搜索调用记录数。
        nonempty_search_calls: 至少产生一个候选命中的搜索调用数。
        empty_search_calls: 没有候选命中的搜索调用数。
        search_hits: 搜索返回的 Note 候选记录数，不是调用数。
        reads: 实际打开 Note 正文的记录数。
        read_sessions: 至少读取过一条 Note 的用户会话数。
        read_rate: 以候选命中为分母的读取比例。
        quality: 搜索与读取来源的证据完整度。
    """

    search_calls: int
    nonempty_search_calls: int
    empty_search_calls: int
    search_hits: int
    reads: int
    read_sessions: int
    read_rate: MemoryRatioData
    quality: Quality


class MemoryEffectData(BaseModel):
    """公开 Note 与用户会话对的唯一效果结论。

    Attributes:
        helpful: 判定为产生帮助的 Note-session 对数。
        harmful: 判定为造成误导的 Note-session 对数。
        unused: 明确读到但未用于决策的 Note-session 对数。
        unknown: 无法判断效果的 Note-session 对数，不进入效果分母。
        judged_user_sessions: 当前时间窗内已有判效的用户会话数。
        eligible_user_sessions: 有访问或判效证据的用户会话数。
        judgement_coverage: 已判效用户会话占可判会话的比例。
        helpful_rate: helpful 占 helpful、harmful、unused 总和的比例。
        quality: 判效证据和来源活动日期的完整度。
    """

    helpful: int
    harmful: int
    unused: int
    unknown: int
    judged_user_sessions: int
    eligible_user_sessions: int
    judgement_coverage: MemoryRatioData
    helpful_rate: MemoryRatioData
    quality: Quality


class MemoryRecallData(BaseModel):
    """公开本应使用却未使用的 Memory 召回缺口。

    Attributes:
        retrieval_miss: 检索和 Dictionary 都未召回的条目数。
        awareness_miss: 已出现在线索中但 Agent 没意识到可用的条目数。
        judged_user_sessions: recall miss 比例使用的已判效用户会话分母。
        miss_rate: 两类 miss 合计除以已判效用户会话数的比例。
        quality: 判效覆盖和来源活动日期的完整度。
    """

    retrieval_miss: int
    awareness_miss: int
    judged_user_sessions: int
    miss_rate: MemoryRatioData
    quality: Quality


class MemoryAssetsData(BaseModel):
    """公开当前 Memory 资产规模和 Dictionary 状态。

    Attributes:
        as_of: 当前资产快照的本地日期。
        active_notes: 当前可检索的 active Note 数。
        raw_reads: 未按用户会话过滤的原始读取记录数。
        raw_harmful_outcomes: 原始 harmful outcome 记录数。
        contradicted_or_superseded: 当前矛盾或已被替代的未退休 Note 数。
        harmful_high_notes: harmful 引用达到退休阈值的未退休 Note 数。
        dictionary_status: consistent、stale 或 missing。
        dictionary_updated_at: 最近成功发布或标记 stale 的时间；未知时为 None。
        quality: Note 与 Dictionary 来源的整体完整度。
    """

    as_of: str
    active_notes: int
    raw_reads: int
    raw_harmful_outcomes: int
    contradicted_or_superseded: int
    harmful_high_notes: int
    dictionary_status: Literal["consistent", "stale", "missing"]
    dictionary_updated_at: datetime | None
    quality: Quality


class MemorySourceData(BaseModel):
    """公开一个 Memory 事实源的范围和时间完整性。

    Attributes:
        updated_at: 当前来源最后一个可解析事实时间；未知时为 None。
        sample_start: 本次查询命中样本的最早时间；没有样本时为 None。
        sample_end: 本次查询命中样本的最晚时间；没有样本时为 None。
        sample_size: 本次查询读取的来源记录数。
        unknown_time_records: 因旧格式或损坏而无法归入日期的记录数。
        quality: 当前来源的时间和内容完整度。
    """

    updated_at: datetime | None
    sample_start: datetime | None
    sample_end: datetime | None
    sample_size: int
    unknown_time_records: int
    quality: Quality


class MemoryStatisticsData(BaseModel):
    """公开 Memory 页所需的时间窗、四类指标、资产和来源。

    Attributes:
        generated_at: 本次只读快照生成时间。
        window_start: 查询包含的开始时刻。
        window_end: 查询不包含的结束时刻。
        timezone: 调用方选择的 IANA 时区。
        sample_size: 查询窗内可解码访问记录数，不混入判效或资产数量。
        quality: 全部 Memory 来源中的最低质量。
        freshness: 与其他 Statistics API 共用的来源新鲜度摘要。
        sources: 每个 Memory 来源的样本范围、数量和质量。
        attribution: 访问事件归因覆盖。
        retrieval: 搜索、候选命中和实际读取。
        effect: Note-session 对的效果分类。
        recall: retrieval miss 与 awareness miss。
        assets: 当前 Note、日志和 Dictionary 资产快照。
    """

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    timezone: str
    sample_size: int
    quality: Quality
    freshness: dict[str, SourceFreshness]
    sources: dict[str, MemorySourceData]
    attribution: MemoryAttributionData
    retrieval: MemoryRetrievalData
    effect: MemoryEffectData
    recall: MemoryRecallData
    assets: MemoryAssetsData
