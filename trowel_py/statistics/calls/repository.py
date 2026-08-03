"""用稳定游标从 telemetry.db 读取调用和有限关联 trace 图。"""

from __future__ import annotations

import base64
import json
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from trowel_py.statistics.calls.models import (
    StoredCallPage,
    StoredCallSpan,
    StoredTraceGraph,
    StoredTraceLink,
)
from trowel_py.telemetry.contracts import datetime_to_epoch_ns
from trowel_py.telemetry.storage import TelemetryDatabase

_CURSOR_BYTES = 16


class CallStatisticsReader:
    """为调用列表和详情查询创建独立短连接。"""

    def __init__(self, database: TelemetryDatabase) -> None:
        """保存 telemetry.db 所有者，不跨线程共享 SQLite 连接。

        Args:
            database: 已初始化且只由 Python sidecar 拥有的遥测数据库。
        """

        self._database = database

    def list_calls(
        self,
        start: datetime,
        end: datetime,
        *,
        component: str | None,
        operation: str | None,
        runtime: str | None,
        status: str | None,
        minimum_duration_ms: float,
        limit: int,
        cursor: str | None,
    ) -> StoredCallPage:
        """按开始时间和 span ID 倒序读取一页调用。

        Args:
            start: 包含边界的查询开始时刻。
            end: 不包含边界的查询结束时刻。
            component: 可选受控组件筛选。
            operation: 可选受控操作筛选。
            runtime: 可选原生 runtime 筛选。
            status: 可选终态筛选。
            minimum_duration_ms: 调用耗时下限，包含边界。
            limit: 当前页最多返回的调用数。
            cursor: 上一页最后一条调用编码出的稳定起点。

        Returns:
            当前页调用和可选下一页游标。

        Raises:
            ValueError: cursor 格式无效。
        """

        conditions = [
            "started_at_ns >= ?",
            "started_at_ns < ?",
            "duration_ms >= ?",
        ]
        parameters: list[object] = [
            datetime_to_epoch_ns(start),
            datetime_to_epoch_ns(end),
            minimum_duration_ms,
        ]
        if component is not None:
            conditions.append("component = ?")
            parameters.append(component)
        if operation is not None:
            conditions.append("operation = ?")
            parameters.append(operation)
        if runtime is not None:
            conditions.append("runtime = ?")
            parameters.append(runtime)
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)
        if cursor is not None:
            cursor_started_at_ns, cursor_span_id = _decode_cursor(cursor)
            conditions.append(
                "(started_at_ns < ? OR (started_at_ns = ? AND span_id < ?))"
            )
            parameters.extend(
                (cursor_started_at_ns, cursor_started_at_ns, cursor_span_id)
            )
        parameters.append(limit + 1)
        connection = self._database.connect_reader()
        try:
            rows = connection.execute(
                f"""
                SELECT trace_id, span_id, parent_span_id, started_at_ns,
                       ended_at_ns, duration_ms, component, operation, status,
                       runtime, attributes_json
                FROM raw_spans
                WHERE {" AND ".join(conditions)}
                ORDER BY started_at_ns DESC, span_id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        finally:
            connection.close()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = tuple(_span_from_row(row) for row in visible)
        next_cursor = (
            _encode_cursor(items[-1].started_at_ns, items[-1].span_id)
            if has_more and items
            else None
        )
        return StoredCallPage(items=items, next_cursor=next_cursor)

    def read_trace_graph(
        self,
        trace_id: bytes,
        *,
        max_traces: int = 16,
        max_spans: int = 512,
    ) -> StoredTraceGraph:
        """沿 span link 双向读取有限关联图，不根据时间猜测关系。

        Args:
            trace_id: 用户选中调用所在的 16 字节 trace 身份。
            max_traces: 最多跟随的关联 trace 数，防止坏图无界扩张。
            max_spans: 最多返回的 span 数。

        Returns:
            有序 span 图；超过任一上限时标记 truncated。
        """

        connection = self._database.connect_reader()
        try:
            return _read_trace_graph(
                connection,
                trace_id,
                max_traces=max(max_traces, 1),
                max_spans=max(max_spans, 1),
            )
        finally:
            connection.close()


def _read_trace_graph(
    connection: sqlite3.Connection,
    trace_id: bytes,
    *,
    max_traces: int,
    max_spans: int,
) -> StoredTraceGraph:
    """在一个只读快照中完成有限双向 BFS。"""

    pending = [trace_id]
    visited: set[bytes] = set()
    spans: dict[bytes, StoredCallSpan] = {}
    truncated = False
    while pending:
        current_trace = pending.pop(0)
        if current_trace in visited:
            continue
        if len(visited) >= max_traces:
            truncated = True
            break
        visited.add(current_trace)
        rows = connection.execute(
            """
            SELECT trace_id, span_id, parent_span_id, started_at_ns,
                   ended_at_ns, duration_ms, component, operation, status,
                   runtime, attributes_json
            FROM raw_spans
            WHERE trace_id = ?
            ORDER BY started_at_ns, span_id
            """,
            (current_trace,),
        ).fetchall()
        remaining = max_spans - len(spans)
        if len(rows) > remaining:
            rows = rows[:remaining]
            truncated = True
        current_spans = [_span_from_row(row) for row in rows]
        if not current_spans:
            continue
        span_ids = [span.span_id for span in current_spans]
        links = _links_for_spans(connection, span_ids)
        for span in current_spans:
            span_links = tuple(links.get(span.span_id, ()))
            spans[span.span_id] = replace(span, links=span_links)
            for link in span_links:
                if link.trace_id not in visited and link.trace_id not in pending:
                    pending.append(link.trace_id)
        for reverse_trace in _reverse_link_source_traces(connection, current_trace):
            if reverse_trace not in visited and reverse_trace not in pending:
                pending.append(reverse_trace)
        if len(spans) >= max_spans and pending:
            truncated = True
            break
    ordered = tuple(
        sorted(spans.values(), key=lambda span: (span.started_at_ns, span.span_id))
    )
    return StoredTraceGraph(spans=ordered, truncated=truncated)


def _links_for_spans(
    connection: sqlite3.Connection,
    span_ids: Sequence[bytes],
) -> dict[bytes, list[StoredTraceLink]]:
    """批量读取指定来源 span 的有序 link。"""

    if not span_ids:
        return {}
    placeholders = ",".join("?" for _ in span_ids)
    rows = connection.execute(
        f"""
        SELECT span_id, link_index, linked_trace_id, linked_span_id
        FROM span_links
        WHERE span_id IN ({placeholders})
        ORDER BY span_id, link_index
        """,
        tuple(span_ids),
    ).fetchall()
    result: dict[bytes, list[StoredTraceLink]] = {}
    for row in rows:
        source_span_id = bytes(row["span_id"])
        result.setdefault(source_span_id, []).append(
            StoredTraceLink(
                source_span_id=source_span_id,
                link_index=int(row["link_index"]),
                trace_id=bytes(row["linked_trace_id"]),
                span_id=(
                    bytes(row["linked_span_id"])
                    if row["linked_span_id"] is not None
                    else None
                ),
            )
        )
    return result


def _reverse_link_source_traces(
    connection: sqlite3.Connection,
    linked_trace_id: bytes,
) -> tuple[bytes, ...]:
    """返回所有明确 link 到目标 trace 的来源 trace。"""

    rows = connection.execute(
        """
        SELECT DISTINCT source.trace_id
        FROM span_links AS link
        JOIN raw_spans AS source ON source.span_id = link.span_id
        WHERE link.linked_trace_id = ?
        ORDER BY source.trace_id
        """,
        (linked_trace_id,),
    ).fetchall()
    return tuple(bytes(row["trace_id"]) for row in rows)


def _span_from_row(row: sqlite3.Row) -> StoredCallSpan:
    """把一行受控原始 span 转成不含 session/call 摘要的值对象。"""

    attributes, valid = _decode_attributes(str(row["attributes_json"]))
    return StoredCallSpan(
        trace_id=bytes(row["trace_id"]),
        span_id=bytes(row["span_id"]),
        parent_span_id=(
            bytes(row["parent_span_id"]) if row["parent_span_id"] is not None else None
        ),
        started_at_ns=int(row["started_at_ns"]),
        ended_at_ns=int(row["ended_at_ns"]),
        duration_ms=float(row["duration_ms"]),
        component=str(row["component"]),
        operation=str(row["operation"]),
        status=str(row["status"]),
        runtime=str(row["runtime"]) or None,
        attributes=attributes,
        attributes_valid=valid,
    )


def _decode_attributes(value: str) -> tuple[dict[str, Any], bool]:
    """只接受 JSON object；损坏数据由 service 显式降级。"""

    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}, False
    return (dict(decoded), True) if isinstance(decoded, dict) else ({}, False)


def _encode_cursor(started_at_ns: int, span_id: bytes) -> str:
    """把唯一排序键编码成不含数据库路径或正文的 URL-safe 游标。"""

    payload = struct.pack(">Q", started_at_ns) + span_id
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[int, bytes]:
    """解码并严格校验调用列表游标。"""

    try:
        padding = "=" * (-len(value) % 4)
        payload = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid calls cursor") from exc
    if len(payload) != _CURSOR_BYTES:
        raise ValueError("invalid calls cursor")
    return struct.unpack(">Q", payload[:8])[0], payload[8:]
