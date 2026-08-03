"""验证 telemetry.db 的独立 schema、幂等聚合与保留策略。"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from tests.telemetry.support import BASE_TIME, batch_request, metric_payload, span_payload
from trowel_py.telemetry.contracts import prepare_batch
from trowel_py.telemetry.storage import TelemetryDatabase


def _database(tmp_path: Path) -> TelemetryDatabase:
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    return database


def test_initialize_creates_wal_database_and_versioned_schema(tmp_path: Path) -> None:
    database = _database(tmp_path)

    with database.connect_reader() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
        checkpoint = connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0]

    assert {
        "telemetry_batches",
        "raw_spans",
        "span_links",
        "raw_metrics",
        "hourly_span_stats",
        "daily_span_stats",
        "telemetry_watermarks",
    }.issubset(tables)
    assert journal_mode == "wal"
    assert synchronous == 1
    assert checkpoint == 1000


def test_write_is_idempotent_and_rejects_batch_id_conflicts(tmp_path: Path) -> None:
    database = _database(tmp_path)
    original = prepare_batch(batch_request())
    conflicting = prepare_batch(
        batch_request(spans=[span_payload(2, duration_ms=99.0)])
    )

    with database.open_writer() as writer:
        first = writer.write_batches([original])
        duplicate = writer.write_batches([original])
        conflict = writer.write_batches([conflicting])

    assert first.inserted_records == 1
    assert duplicate.duplicate_batches == 1
    assert duplicate.inserted_records == 0
    assert conflict.conflicting_batches == 1
    assert database.reader().raw_counts() == {"spans": 1, "metrics": 0}


def test_hour_and_day_aggregation_merge_histograms_without_double_counting(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    spans = [
        span_payload(
            index + 1,
            component="runtime",
            operation="runtime.call",
            started_at=BASE_TIME + timedelta(minutes=index * 5),
            duration_ms=duration,
        )
        for index, duration in enumerate((1.0, 8.0, 120.0, 1400.0))
    ]
    prepared = prepare_batch(batch_request(spans=spans))
    through = BASE_TIME + timedelta(days=1)

    with database.open_writer() as writer:
        writer.write_batches([prepared])
        first = writer.aggregate(through)
        second = writer.aggregate(through)

    rows = database.reader().query_span_aggregates(
        BASE_TIME - timedelta(hours=1),
        through,
        resolution="hour",
    )
    daily = database.reader().query_span_aggregates(
        BASE_TIME.replace(hour=0),
        through,
        resolution="day",
    )

    assert first.hourly_rows > 0
    assert second.hourly_rows == first.hourly_rows
    assert sum(row.sample_count for row in rows) == 4
    assert sum(sum(row.histogram_counts) for row in rows) == 4
    assert sum(row.sample_count for row in daily) == 4
    assert database.reader().watermarks().keys() >= {"raw", "hour", "day"}


def test_aggregation_preserves_runtime_reported_model_name(tmp_path: Path) -> None:
    """未预登记的真实模型名仍能成为可查询的低基数分组。"""

    database = _database(tmp_path)
    span = span_payload(20, component="runtime", operation="runtime.call")
    span.update({"runtime": "codex", "model": "future-model-native"})
    prepared = prepare_batch(batch_request(spans=[span]))
    through = BASE_TIME + timedelta(days=1)

    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.aggregate(through)

    rows = database.reader().query_span_aggregates(
        BASE_TIME - timedelta(hours=1),
        through,
        resolution="hour",
    )

    assert [(row.model, row.sample_count) for row in rows] == [
        ("future-model-native", 1),
    ]


def test_metric_aggregation_is_idempotent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    prepared = prepare_batch(
        batch_request(spans=[], metrics=[metric_payload(1), metric_payload(2)])
    )

    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.aggregate(BASE_TIME + timedelta(days=1))
        writer.aggregate(BASE_TIME + timedelta(days=1))

    rows = database.reader().query_metric_aggregates(
        BASE_TIME - timedelta(hours=1),
        BASE_TIME + timedelta(days=1),
        resolution="hour",
    )
    assert sum(row.sample_count for row in rows) == 2
    assert sum(row.value_sum for row in rows) == 3.0


def test_cleanup_keeps_complete_source_days_for_repeatable_daily_aggregation(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    now = BASE_TIME
    retained_day = (now - timedelta(days=90)).replace(hour=0)
    spans = [
        span_payload(index + 1, started_at=retained_day + timedelta(hours=index))
        for index in range(24)
    ]
    prepared = prepare_batch(batch_request(spans=spans))

    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.aggregate(now + timedelta(days=1))
        writer.cleanup(now)
        writer.aggregate_daily(now + timedelta(days=1))

    rows = database.reader().query_span_aggregates(
        retained_day,
        retained_day + timedelta(days=1),
        resolution="day",
    )
    assert sum(row.sample_count for row in rows) == 24


def test_cleanup_keeps_complete_source_hours_for_repeatable_hourly_aggregation(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    now = BASE_TIME + timedelta(minutes=30)
    retained_hour = (now - timedelta(days=14)).replace(minute=0)
    spans = [
        span_payload(index + 1, started_at=retained_hour + timedelta(minutes=index))
        for index in range(60)
    ]
    prepared = prepare_batch(batch_request(spans=spans))

    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.aggregate_hourly(now + timedelta(hours=1))
        writer.cleanup(now)
        writer.aggregate_hourly(now + timedelta(hours=1))

    rows = database.reader().query_span_aggregates(
        retained_hour,
        retained_hour + timedelta(hours=1),
        resolution="hour",
    )
    assert sum(row.sample_count for row in rows) == 60


def test_cleanup_keeps_recent_deduplication_metadata_for_old_batch_headers(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    request = batch_request().model_copy(
        update={"collected_at": BASE_TIME - timedelta(days=20)}
    )
    prepared = prepare_batch(request)

    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.cleanup(BASE_TIME)
        retried = writer.write_batches([prepared])

    assert retried.duplicate_batches == 1
    assert retried.inserted_records == 0


def test_cleanup_obeys_three_retention_windows_and_leaves_business_db_unchanged(
    tmp_path: Path,
) -> None:
    business_path = tmp_path / "sessions.db"
    business = sqlite3.connect(business_path)
    business.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
    business.execute("INSERT INTO sentinel VALUES ('keep')")
    business.commit()
    business.close()
    database = _database(tmp_path)
    old_span = span_payload(
        9,
        started_at=BASE_TIME - timedelta(days=20),
    )
    prepared = prepare_batch(batch_request(spans=[old_span]))

    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.aggregate(BASE_TIME)
        report = writer.cleanup(BASE_TIME)

    business = sqlite3.connect(business_path)
    sentinel = business.execute("SELECT value FROM sentinel").fetchone()[0]
    business.close()

    assert report.raw_spans_deleted == 1
    assert database.reader().raw_counts()["spans"] == 0
    assert sentinel == "keep"
    assert database.reader().database_sizes()["database"] > 0
