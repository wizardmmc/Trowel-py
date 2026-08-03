"""用 L01 production-shape 字段基数验证 10 万条遥测预算。"""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import pytest

from tests.telemetry.support import (
    BASE_TIME,
    COMPONENT_OPERATIONS,
    batch_request,
    span_payload,
)
from trowel_py.statistics.calls.repository import CallStatisticsReader
from trowel_py.statistics.calls.service import build_call_detail, build_call_list
from trowel_py.statistics.window import StatisticsWindow
from trowel_py.telemetry.contracts import prepare_batch
from trowel_py.telemetry.storage import TelemetryDatabase


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = int((len(ordered) - 1) * quantile)
    return ordered[position]


@pytest.mark.benchmark
def test_production_shape_100k_write_aggregate_cleanup_and_query_budget(
    tmp_path: Path,
) -> None:
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    transaction_ms: list[float] = []
    row_count = 100_000
    batch_size = 250
    source_window_days = 21
    with database.open_writer() as writer:
        for batch_index, start in enumerate(range(0, row_count, batch_size)):
            spans = []
            for index in range(start, start + batch_size):
                component, operation = COMPONENT_OPERATIONS[
                    index % len(COMPONENT_OPERATIONS)
                ]
                timestamp = (
                    BASE_TIME
                    - timedelta(days=source_window_days)
                    + timedelta(
                        seconds=index * (source_window_days * 86_400 / row_count)
                    )
                )
                span = span_payload(
                    index + 1,
                    component=component,
                    operation=operation,
                    started_at=timestamp,
                    duration_ms=1.0 + ((index * 37) % 20_000) / 10.0,
                )
                if index > 0 and index % 20 == 0:
                    span["links"] = [
                        {
                            "trace_id": f"{index:032x}",
                            "span_id": f"{index:016x}",
                        }
                    ]
                spans.append(span)
            prepared = prepare_batch(
                batch_request(
                    f"perf-{batch_index:08d}",
                    spans=spans,
                )
            )
            started = time.perf_counter()
            writer.write_batches([prepared])
            transaction_ms.append((time.perf_counter() - started) * 1000)

        aggregate_ms: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            writer.aggregate_hourly(BASE_TIME + timedelta(days=1))
            aggregate_ms.append((time.perf_counter() - started) * 1000)
        writer.aggregate_daily(BASE_TIME + timedelta(days=1))

    query_ms: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        rows = database.reader().query_span_aggregates(
            BASE_TIME - timedelta(days=90),
            BASE_TIME + timedelta(days=1),
            resolution="hour",
        )
        query_ms.append((time.perf_counter() - started) * 1000)

    call_reader = CallStatisticsReader(database)
    call_window = StatisticsWindow(
        start=BASE_TIME - timedelta(days=90),
        end=BASE_TIME + timedelta(days=1),
        timezone="UTC",
    )
    call_list_ms: list[float] = []
    call_page = None
    for _ in range(20):
        started = time.perf_counter()
        call_page = build_call_list(
            call_reader,
            call_window,
            component="sqlite",
            minimum_duration_ms=500,
            limit=50,
        )
        call_list_ms.append((time.perf_counter() - started) * 1000)
    assert call_page is not None and call_page.items

    call_detail_ms: list[float] = []
    call_detail = None
    linked_span_index = row_count - ((row_count - 1) % 20)
    for _ in range(20):
        started = time.perf_counter()
        call_detail = build_call_detail(call_reader, f"{linked_span_index:032x}")
        call_detail_ms.append((time.perf_counter() - started) * 1000)

    with database.open_writer() as writer:
        started = time.perf_counter()
        cleanup = writer.cleanup(BASE_TIME + timedelta(days=1))
        cleanup_ms = (time.perf_counter() - started) * 1000

    measurements = {
        "write_p50_ms": _percentile(transaction_ms, 0.50),
        "write_p95_ms": _percentile(transaction_ms, 0.95),
        "write_max_ms": max(transaction_ms),
        "write_over_10ms": sum(value >= 10 for value in transaction_ms),
        "aggregate_p95_ms": _percentile(aggregate_ms, 0.95),
        "aggregate_runs_ms": aggregate_ms,
        "query_p95_ms": _percentile(query_ms, 0.95),
        "call_list_p95_ms": _percentile(call_list_ms, 0.95),
        "call_detail_p95_ms": _percentile(call_detail_ms, 0.95),
        "cleanup_ms": cleanup_ms,
    }
    print(measurements)
    assert rows
    assert call_detail is not None
    assert measurements["write_p95_ms"] < 10, measurements
    assert measurements["aggregate_p95_ms"] < 100, measurements
    assert measurements["query_p95_ms"] < 300, measurements
    assert measurements["call_list_p95_ms"] < 300, measurements
    assert measurements["call_detail_p95_ms"] < 300, measurements
    assert measurements["cleanup_ms"] < 250, measurements
    assert cleanup.raw_spans_deleted > 0


@pytest.mark.benchmark
def test_90_day_hourly_aggregate_query_budget(tmp_path: Path) -> None:
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    rows: list[tuple[object, ...]] = []
    for hour in range(90 * 24):
        component, operation = COMPONENT_OPERATIONS[hour % len(COMPONENT_OPERATIONS)]
        bucket_start = BASE_TIME - timedelta(hours=hour + 1)
        sample_count = 10
        rows.append(
            (
                int(bucket_start.timestamp() * 1_000_000_000),
                component,
                operation,
                "ok",
                "",
                "",
                sample_count,
                float(sample_count * 25),
                1.0,
                100.0,
                sample_count,
                0,
                0,
                0,
                0,
            )
        )
    connection = database.connect_reader()
    try:
        connection.executemany(
            """
            INSERT INTO hourly_span_stats (
                bucket_start_ns, component, operation, status, runtime, model,
                sample_count, duration_sum_ms, duration_min_ms, duration_max_ms,
                histogram_p0, histogram_p1, histogram_p2, histogram_p3,
                histogram_p4
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()

    query_ms: list[float] = []
    result = []
    for _ in range(30):
        started = time.perf_counter()
        result = database.reader().query_span_aggregates(
            BASE_TIME - timedelta(days=90),
            BASE_TIME,
            resolution="hour",
        )
        query_ms.append((time.perf_counter() - started) * 1000)

    print({"query_90_day_p95_ms": _percentile(query_ms, 0.95), "rows": len(result)})
    assert len(result) == 90 * 24
    assert _percentile(query_ms, 0.95) < 300
