"""验证 collector 热路径、数据库失败和关闭竞争。"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.telemetry.support import batch_request, span_payload
from trowel_py.telemetry.collector import TelemetryCollector
from trowel_py.telemetry.contracts import prepare_batch
from trowel_py.telemetry.storage import TelemetryDatabase


def _collector(
    tmp_path: Path,
    **overrides: object,
) -> tuple[TelemetryDatabase, TelemetryCollector]:
    database = TelemetryDatabase(tmp_path / "telemetry.db", busy_timeout_ms=10)
    database.initialize()
    collector = TelemetryCollector(
        database.open_writer,
        recent_batches=database.reader().recent_batch_fingerprints(),
        flush_interval_seconds=0.02,
        maintenance_interval_seconds=3600,
        **overrides,
    )
    collector.start()
    return database, collector


def test_collector_batches_small_submissions_and_drains_on_close(tmp_path: Path) -> None:
    database, collector = _collector(tmp_path, flush_size=250)

    for index in range(10):
        result = collector.submit(
            batch_request(f"batch-{index:04d}", spans=[span_payload(index + 1)])
        )
        assert result.accepted == 1
    report = collector.close(timeout_seconds=1.0)

    assert report.drained is True
    assert report.dropped == 0
    assert database.reader().raw_counts()["spans"] == 10


def test_duplicate_batch_is_reported_without_entering_queue_twice(tmp_path: Path) -> None:
    database, collector = _collector(tmp_path)
    request = batch_request()

    first = collector.submit(request)
    duplicate = collector.submit(request)
    collector.close(timeout_seconds=1.0)

    assert first.accepted == 1
    assert duplicate.duplicate is True
    assert duplicate.accepted == 0
    assert database.reader().raw_counts()["spans"] == 1


def test_queue_capacity_drops_telemetry_without_blocking_submit(tmp_path: Path) -> None:
    gate = threading.Event()

    class BlockingWriter:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write_batches(self, batches):
            gate.wait(1.0)
            return type(
                "Report",
                (),
                {"inserted_records": sum(item.accepted_count for item in batches),
                 "duplicate_batches": 0,
                 "conflicting_batches": 0},
            )()

        def aggregate(self, _through):
            return None

        def cleanup(self, _now):
            return None

    collector = TelemetryCollector(
        BlockingWriter,
        queue_capacity=1,
        flush_interval_seconds=0.01,
        maintenance_interval_seconds=3600,
    )
    collector.start()
    collector.submit(batch_request("batch-first"))
    time.sleep(0.03)
    collector.submit(batch_request("batch-second"))
    started = time.perf_counter()
    result = collector.submit(batch_request("batch-third"))
    elapsed = time.perf_counter() - started
    gate.set()
    collector.close(timeout_seconds=1.0)

    assert result.dropped == 1
    assert result.error_categories == {"queue_full": 1}
    assert elapsed < 0.05


def test_idle_collector_aggregates_raw_records_left_by_an_earlier_process(
    tmp_path: Path,
) -> None:
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    recorded_at = datetime.now(UTC) - timedelta(hours=2)
    with database.open_writer() as writer:
        writer.write_batches(
            [
                prepare_batch(
                    batch_request(spans=[span_payload(started_at=recorded_at)])
                )
            ]
        )
    checkpoint_requested = threading.Event()
    collector = TelemetryCollector(
        database.open_writer,
        flush_interval_seconds=0.01,
        maintenance_interval_seconds=0.01,
        checkpoint_requester=checkpoint_requested.set,
    )

    collector.start()
    deadline = time.monotonic() + 1.0
    watermarks = database.reader().watermarks()
    while "day" not in watermarks and time.monotonic() < deadline:
        time.sleep(0.01)
        watermarks = database.reader().watermarks()
    report = collector.close(timeout_seconds=1.0)

    assert report.drained is True
    assert checkpoint_requested.is_set()
    assert watermarks.keys() >= {"raw", "hour", "day"}
    rows = database.reader().query_span_aggregates(
        recorded_at - timedelta(days=1),
        recorded_at + timedelta(days=1),
        resolution="day",
    )
    assert sum(row.sample_count for row in rows) == 1


def test_database_busy_drops_batch_and_worker_continues(tmp_path: Path) -> None:
    database, collector = _collector(tmp_path, busy_retries=0)
    blocker = database.connect_reader()
    blocker.execute("BEGIN IMMEDIATE")

    collector.submit(batch_request("batch-busy"))
    time.sleep(0.08)
    blocker.rollback()
    blocker.close()
    collector.submit(batch_request("batch-after-busy", spans=[span_payload(2)]))
    report = collector.close(timeout_seconds=1.0)

    assert report.dropped >= 1
    assert database.reader().raw_counts()["spans"] == 1


def test_close_timeout_returns_without_waiting_for_stuck_writer() -> None:
    gate = threading.Event()

    class StuckWriter:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write_batches(self, _batches):
            gate.wait(5.0)

        def aggregate(self, _through):
            return None

        def cleanup(self, _now):
            return None

    collector = TelemetryCollector(
        StuckWriter,
        flush_interval_seconds=0.01,
        maintenance_interval_seconds=3600,
    )
    collector.start()
    collector.submit(batch_request("batch-stuck"))
    time.sleep(0.03)

    started = time.perf_counter()
    report = collector.close(timeout_seconds=0.02)
    elapsed = time.perf_counter() - started
    gate.set()

    assert report.drained is False
    assert report.dropped >= 1
    assert elapsed < 0.1
