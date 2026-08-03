"""校验调用查询，并把有限原始 span 图转换成安全公开详情。"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import cast

from trowel_py.statistics.calls.models import StoredCallSpan
from trowel_py.statistics.calls.repository import CallStatisticsReader
from trowel_py.statistics.calls.schemas import (
    CallDetailData,
    CallListData,
    CallListItemData,
    CallQuality,
    CallSpanData,
    CallStatus,
    CallTraceLinkData,
    UnavailableIntervalData,
)
from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.window import StatisticsWindow
from trowel_py.telemetry.catalog import COMPONENTS, OPERATIONS, RUNTIMES, STATUSES
from trowel_py.telemetry.storage import epoch_ns_to_datetime

_TRACE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_QUALITY_ORDER = {"unavailable": 0, "partial": 1, "reliable": 2}
_PUBLIC_NUMERIC_ATTRIBUTES = frozenset({"retry_count"})


def build_call_list(
    reader: CallStatisticsReader,
    window: StatisticsWindow,
    *,
    component: str | None = None,
    operation: str | None = None,
    runtime: str | None = None,
    status: str | None = None,
    minimum_duration_ms: float = 0,
    limit: int = 50,
    cursor: str | None = None,
) -> CallListData:
    """校验筛选并生成一页不含 session、参数或正文的调用。

    Args:
        reader: 只读取隔离 telemetry.db 的调用仓储。
        window: 已按调用方时区解析的半开时间窗。
        component: 可选受控组件。
        operation: 可选受控操作名。
        runtime: 可选 Claude Code 或 Codex 分类。
        status: 可选 ok、error 或 unset。
        minimum_duration_ms: 包含边界的非负耗时下限。
        limit: 1 至 200 的页面大小。
        cursor: 上一页返回的稳定游标。

    Returns:
        共用时间元数据、当前页调用和下一页游标。

    Raises:
        ValueError: 任一筛选、页面大小或游标无效。
    """

    _validate_filters(
        component=component,
        operation=operation,
        runtime=runtime,
        status=status,
        minimum_duration_ms=minimum_duration_ms,
        limit=limit,
    )
    page = reader.list_calls(
        window.start,
        window.end,
        component=component,
        operation=operation,
        runtime=runtime,
        status=status,
        minimum_duration_ms=minimum_duration_ms,
        limit=limit,
        cursor=cursor,
    )
    items = [_list_item(span) for span in page.items]
    quality = _combine_quality([item.quality for item in items])
    updated_at = max((span.ended_at_ns for span in page.items), default=None)
    return CallListData(
        generated_at=datetime.now(UTC),
        window_start=window.start,
        window_end=window.end,
        timezone=window.timezone,
        sample_size=len(items),
        quality=quality,
        freshness=_freshness(updated_at),
        items=items,
        next_cursor=page.next_cursor,
    )


def build_call_detail(
    reader: CallStatisticsReader,
    trace_id: str,
) -> CallDetailData | None:
    """读取选中 trace 及 link 可达图，并把坏关系降级成 unavailable。

    Args:
        reader: 只读取隔离 telemetry.db 的调用仓储。
        trace_id: 调用列表返回的 16 字节十六进制随机身份。

    Returns:
        安全、有限且无父环的详情；找不到选中 trace 时为 None。

    Raises:
        ValueError: trace_id 不是固定长度十六进制。
    """

    if not _TRACE_ID_PATTERN.fullmatch(trace_id):
        raise ValueError("trace_id must be 16-byte hex")
    selected_trace_id = bytes.fromhex(trace_id)
    stored = reader.read_trace_graph(selected_trace_id)
    selected = [span for span in stored.spans if span.trace_id == selected_trace_id]
    if not selected:
        return None

    by_key = {(span.trace_id, span.span_id): span for span in stored.spans}
    sanitized_parents: dict[tuple[bytes, bytes], tuple[bytes, bytes] | None] = {}
    gaps: list[UnavailableIntervalData] = []
    for span in stored.spans:
        span_key = (span.trace_id, span.span_id)
        parent = span.parent_span_id
        if not span.attributes_valid:
            gaps.append(_gap("invalid_attributes", span))
        if parent is None:
            sanitized_parents[span_key] = None
            continue
        parent_key = (span.trace_id, parent)
        parent_span = by_key.get(parent_key)
        if parent_span is None:
            sanitized_parents[span_key] = None
            gaps.append(_gap("missing_parent", span))
        else:
            sanitized_parents[span_key] = parent_key

    cycle_nodes = _parent_cycle_nodes(sanitized_parents)
    for span_key in sorted(cycle_nodes):
        sanitized_parents[span_key] = None
        gaps.append(_gap("parent_cycle", by_key[span_key]))

    public_spans: list[CallSpanData] = []
    for span in stored.spans:
        span_key = (span.trace_id, span.span_id)
        links: list[CallTraceLinkData] = []
        seen_links: set[tuple[bytes, bytes | None]] = set()
        for link in span.links:
            identity = (link.trace_id, link.span_id)
            if identity in seen_links:
                gaps.append(_gap("duplicate_link", span))
                continue
            seen_links.add(identity)
            available = (
                (link.trace_id, link.span_id) in by_key
                if link.span_id is not None
                else any(item.trace_id == link.trace_id for item in stored.spans)
            )
            if not available:
                gaps.append(_gap("missing_link_target", span))
            links.append(
                CallTraceLinkData(
                    trace_id=link.trace_id.hex(),
                    span_id=link.span_id.hex() if link.span_id is not None else None,
                    available=available,
                )
            )
        if (
            span.operation == "runtime.call"
            and span.attributes.get("black_box") is True
        ):
            gaps.append(_gap("native_runtime_black_box", span, bounded=False))
        sanitized_parent = sanitized_parents[span_key]
        public_spans.append(
            CallSpanData(
                trace_id=span.trace_id.hex(),
                span_id=span.span_id.hex(),
                parent_span_id=(
                    sanitized_parent[1].hex() if sanitized_parent is not None else None
                ),
                links=links,
                component=span.component,
                operation=span.operation,
                started_at=epoch_ns_to_datetime(span.started_at_ns),
                ended_at=epoch_ns_to_datetime(span.ended_at_ns),
                duration_ms=span.duration_ms,
                status=_status(span.status),
                runtime=span.runtime,
                attributes=_numeric_attributes(span),
            )
        )
    if stored.truncated:
        gaps.append(
            UnavailableIntervalData(
                code="graph_truncated",
                reason=_gap_reason("graph_truncated"),
                source_span_id=None,
                started_at=None,
                ended_at=None,
            )
        )

    root = min(
        (
            span
            for span in selected
            if sanitized_parents[(span.trace_id, span.span_id)] is None
        ),
        key=lambda span: (span.started_at_ns, span.span_id),
        default=min(selected, key=lambda span: (span.started_at_ns, span.span_id)),
    )
    quality = (
        "partial"
        if gaps
        else _combine_quality([_span_quality(span) for span in stored.spans])
    )
    return CallDetailData(
        generated_at=datetime.now(UTC),
        trace_id=selected_trace_id.hex(),
        started_at=epoch_ns_to_datetime(min(span.started_at_ns for span in selected)),
        ended_at=epoch_ns_to_datetime(max(span.ended_at_ns for span in selected)),
        root_operation=root.operation,
        status=_status(root.status),
        quality=quality,
        sample_size=len(public_spans),
        freshness=_freshness(max(span.ended_at_ns for span in stored.spans)),
        spans=public_spans,
        unavailable=_deduplicate_gaps(gaps),
    )


def _validate_filters(
    *,
    component: str | None,
    operation: str | None,
    runtime: str | None,
    status: str | None,
    minimum_duration_ms: float,
    limit: int,
) -> None:
    """拒绝动态标签、非有限耗时和异常页面大小。"""

    if component is not None and component not in COMPONENTS:
        raise ValueError("unsupported component")
    if operation is not None and operation not in OPERATIONS:
        raise ValueError("unsupported operation")
    if runtime is not None and runtime not in RUNTIMES:
        raise ValueError("unsupported runtime")
    if status is not None and status not in STATUSES:
        raise ValueError("unsupported status")
    if not math.isfinite(minimum_duration_ms) or minimum_duration_ms < 0:
        raise ValueError("minimum_duration_ms must be finite and non-negative")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")


def _list_item(span: StoredCallSpan) -> CallListItemData:
    """把仓储 span 转成最近调用表的一行。"""

    return CallListItemData(
        trace_id=span.trace_id.hex(),
        span_id=span.span_id.hex(),
        started_at=epoch_ns_to_datetime(span.started_at_ns),
        duration_ms=span.duration_ms,
        status=_status(span.status),
        component=span.component,
        operation=span.operation,
        runtime=span.runtime,
        quality=_span_quality(span),
    )


def _span_quality(span: StoredCallSpan) -> CallQuality:
    """从受控 quality 和黑盒标记生成单 span 质量。"""

    if not span.attributes_valid:
        return "partial"
    quality = span.attributes.get("quality")
    if quality not in _QUALITY_ORDER:
        quality = "reliable"
    if span.attributes.get("black_box") is True and quality == "reliable":
        return "partial"
    return cast(CallQuality, quality)


def _combine_quality(values: list[CallQuality]) -> CallQuality:
    """空集合为 unavailable，其余取最保守质量。"""

    if not values:
        return "unavailable"
    return min(values, key=lambda value: _QUALITY_ORDER[value])


def _freshness(updated_at_ns: int | None) -> dict[str, SourceFreshness]:
    """用当前响应实际读取到的最新 span 生成来源新鲜度。"""

    return {
        "telemetry": SourceFreshness(
            updated_at=(
                epoch_ns_to_datetime(updated_at_ns)
                if updated_at_ns is not None
                else None
            ),
            status="fresh" if updated_at_ns is not None else "unavailable",
        )
    }


def _parent_cycle_nodes(
    parents: dict[tuple[bytes, bytes], tuple[bytes, bytes] | None],
) -> set[tuple[bytes, bytes]]:
    """返回所有位于 parent 环中的 span，不递归遍历坏图。"""

    cycles: set[tuple[bytes, bytes]] = set()
    completed: set[tuple[bytes, bytes]] = set()
    for start in parents:
        if start in completed:
            continue
        path: list[tuple[bytes, bytes]] = []
        positions: dict[tuple[bytes, bytes], int] = {}
        current: tuple[bytes, bytes] | None = start
        while current is not None and current not in completed:
            if current in positions:
                cycles.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = parents.get(current)
        completed.update(path)
    return cycles


def _numeric_attributes(span: StoredCallSpan) -> dict[str, int | float]:
    """只公开有限数值 allowlist，明确排除 bool 和所有文本字段。"""

    result: dict[str, int | float] = {}
    for key in _PUBLIC_NUMERIC_ATTRIBUTES:
        value = span.attributes.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if math.isfinite(float(value)):
            result[key] = value
    return result


def _status(value: str) -> CallStatus:
    """把损坏的未知终态降为 unset，不把动态文本公开。"""

    return cast(CallStatus, value if value in STATUSES else "unset")


def _gap(
    code: str,
    span: StoredCallSpan,
    *,
    bounded: bool = True,
) -> UnavailableIntervalData:
    """用稳定原因和可选已知边界构造缺口。"""

    return UnavailableIntervalData(
        code=code,
        reason=_gap_reason(code),
        source_span_id=span.span_id.hex(),
        started_at=epoch_ns_to_datetime(span.started_at_ns) if bounded else None,
        ended_at=epoch_ns_to_datetime(span.ended_at_ns) if bounded else None,
    )


def _gap_reason(code: str) -> str:
    """把内部坏图类别映射成不含动态身份的稳定说明。"""

    reasons = {
        "invalid_attributes": "该 span 的属性记录损坏，已隐藏属性。",
        "missing_parent": "父 span 未采集或已过保留期，不能恢复上层区间。",
        "parent_cycle": "父子关系形成环，已按根节点展示。",
        "duplicate_link": "重复 span link 已合并。",
        "missing_link_target": "span link 的目标未采集或已过保留期。",
        "native_runtime_black_box": "原生 runtime 未传播调用上下文，内部调度区间未采集。",
        "graph_truncated": "关联图超过单次详情上限，剩余节点未加载。",
    }
    return reasons[code]


def _deduplicate_gaps(
    gaps: list[UnavailableIntervalData],
) -> list[UnavailableIntervalData]:
    """按类别、来源和边界去除重复坏图报告，并保持首次出现顺序。"""

    result: list[UnavailableIntervalData] = []
    seen: set[tuple[object, ...]] = set()
    for gap in gaps:
        identity = (
            gap.code,
            gap.source_span_id,
            gap.started_at,
            gap.ended_at,
        )
        if identity not in seen:
            seen.add(identity)
            result.append(gap)
    return result
