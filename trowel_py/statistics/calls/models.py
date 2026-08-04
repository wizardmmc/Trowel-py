"""定义调用查询从 telemetry.db 读取的内部值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StoredTraceLink:
    """表示数据库中一条有顺序的非父子 span 关联。

    Attributes:
        source_span_id: 拥有该 link 的 span 身份。
        link_index: 同一来源 span 内的稳定顺序。
        trace_id: 关联目标所在的 trace 身份。
        span_id: 已知时使用的关联目标 span 身份。
    """

    source_span_id: bytes
    link_index: int
    trace_id: bytes
    span_id: bytes | None


@dataclass(frozen=True)
class StoredCallSpan:
    """保存调用列表与详情需要的受控原始 span 字段。

    Attributes:
        trace_id: 当前 span 所在 trace 的随机身份。
        span_id: 当前 span 的随机身份。
        parent_span_id: 真实父上下文；根 span 为 None。
        started_at_ns: UTC Unix epoch 纳秒开始时刻。
        ended_at_ns: UTC Unix epoch 纳秒结束时刻。
        duration_ms: 当前操作的实际毫秒耗时。
        component: 执行操作的受控组件。
        operation: 不含动态值的受控操作名。
        status: ok、error 或 unset。
        runtime: 可选 Claude Code 或 Codex 分类。
        attributes: 已解码的低基数字段；损坏时为空映射。
        attributes_valid: attributes_json 是否成功解码为对象。
        links: 当前 span 的有序 link。
    """

    trace_id: bytes
    span_id: bytes
    parent_span_id: bytes | None
    started_at_ns: int
    ended_at_ns: int
    duration_ms: float
    component: str
    operation: str
    status: str
    runtime: str | None
    attributes: Mapping[str, Any]
    attributes_valid: bool
    links: tuple[StoredTraceLink, ...] = ()


@dataclass(frozen=True)
class StoredCallPage:
    """保存一页按稳定键倒序读取的调用。

    Attributes:
        items: 当前页不超过调用方 limit 的 span。
        next_cursor: 下一页起点；已经到末尾时为 None。
    """

    items: tuple[StoredCallSpan, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class StoredTraceGraph:
    """保存选中 trace 及其双向 link 可达的有限 span 图。

    Attributes:
        spans: 按开始时间与 span ID 排序的有限 span 集合。
        truncated: 关联 trace 或 span 超过安全上限时为 True。
    """

    spans: tuple[StoredCallSpan, ...]
    truncated: bool
