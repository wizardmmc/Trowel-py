"""定义调用列表和跨层 trace 详情的公开去正文 DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from trowel_py.statistics.schemas import SourceFreshness

CallQuality = Literal["reliable", "partial", "unavailable"]
CallStatus = Literal["ok", "error", "unset"]


class CallListItemData(BaseModel):
    """表示最近调用表的一行。

    Attributes:
        trace_id: 用于下钻的随机 trace 身份。
        span_id: 当前列表行对应的随机 span 身份。
        started_at: 当前操作的 UTC 开始时刻。
        duration_ms: 当前操作耗时。
        status: ok、error 或 unset。
        component: 实际记录该操作的受控组件。
        operation: 不含动态值的受控操作名。
        runtime: 可选 Claude Code 或 Codex 分类。
        quality: 当前行是否包含黑盒或损坏缺口。
    """

    trace_id: str
    span_id: str
    started_at: datetime
    duration_ms: float
    status: CallStatus
    component: str
    operation: str
    runtime: str | None
    quality: CallQuality


class CallListData(BaseModel):
    """返回一页调用及其共同时间窗和新鲜度。

    Attributes:
        generated_at: 本次 read model 生成时间。
        window_start: 查询包含的开始时刻。
        window_end: 查询不包含的结束时刻。
        timezone: 调用方选择的 IANA 时区。
        sample_size: 当前页实际返回的调用数。
        quality: 当前页调用的合并质量。
        freshness: telemetry 原始 span 的当前新鲜度。
        items: 按开始时间与稳定 ID 倒序排列的调用。
        next_cursor: 下一页游标；到末尾时为 None。
    """

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    timezone: str
    sample_size: int
    quality: CallQuality
    freshness: dict[str, SourceFreshness]
    items: list[CallListItemData]
    next_cursor: str | None


class CallTraceLinkData(BaseModel):
    """公开一条父子关系之外的 span link。

    Attributes:
        trace_id: 关联目标的随机 trace 身份。
        span_id: 已知时使用的目标 span 身份。
        available: 当前有界详情是否读到了关联目标。
    """

    trace_id: str
    span_id: str | None
    available: bool


class CallSpanData(BaseModel):
    """公开 trace 树中一个不含正文和本机身份的 span。

    Attributes:
        trace_id: 当前 span 所在 trace 的随机身份。
        span_id: 当前 span 的随机身份。
        parent_span_id: 已校验且不会形成环的真实父 span；根节点为 None。
        links: 已去重的非父子关联。
        component: 实际执行操作的受控组件。
        operation: 不含动态值的受控操作名。
        started_at: 当前操作的 UTC 开始时刻。
        ended_at: 当前操作的 UTC 结束时刻。
        duration_ms: 当前操作耗时。
        status: ok、error 或 unset。
        runtime: 可选 Claude Code 或 Codex 分类。
        attributes: allowlist 中的有限数值字段；布尔和文本不公开。
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    links: list[CallTraceLinkData]
    component: str
    operation: str
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    status: CallStatus
    runtime: str | None
    attributes: dict[str, int | float]


class UnavailableIntervalData(BaseModel):
    """说明 trace 中无法可靠还原的一段关系或时间。

    Attributes:
        code: 供前端稳定分组的缺口类别。
        reason: 不含异常正文或动态身份的用户可读原因。
        source_span_id: 能定位到已知 span 时使用的随机身份。
        started_at: 缺口边界可核查时的开始时刻。
        ended_at: 缺口边界可核查时的结束时刻。
    """

    code: str
    reason: str
    source_span_id: str | None
    started_at: datetime | None
    ended_at: datetime | None


class CallDetailData(BaseModel):
    """公开一次调用及 link 可达 span 的有限详情。

    Attributes:
        generated_at: 本次详情生成时间。
        trace_id: 用户选择的随机 trace 身份。
        started_at: 选中 trace 的最早开始时刻。
        ended_at: 选中 trace 的最晚结束时刻。
        root_operation: 选中 trace 最早根 span 的受控操作名。
        status: 根 span 终态；子 span 和关联 trace 保留各自终态。
        quality: 图完整时 reliable；黑盒、坏图或截断时 partial。
        sample_size: 本次有限图中的 span 数。
        freshness: telemetry 原始 span 的新鲜度。
        spans: 已按开始时间排序并消除父环的 span。
        unavailable: 黑盒、断父、坏 link 或截断缺口。
    """

    generated_at: datetime
    trace_id: str
    started_at: datetime
    ended_at: datetime
    root_operation: str
    status: CallStatus
    quality: CallQuality
    sample_size: int
    freshness: dict[str, SourceFreshness]
    spans: list[CallSpanData]
    unavailable: list[UnavailableIntervalData]
