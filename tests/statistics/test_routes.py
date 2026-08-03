"""验证 Statistics API 的统一时间窗、质量和新鲜度响应。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.telemetry.support import BASE_TIME, batch_request, span_payload
from trowel_py.statistics.routes import router
from trowel_py.telemetry.collector import TelemetryCollector
from trowel_py.telemetry.contracts import prepare_batch
from trowel_py.telemetry.storage import TelemetryDatabase


class EmptyAgentReader:
    """返回没有用户 session 的 Agent 统计来源。"""

    def read(self, _window):
        """返回空 session 集合。"""

        return []


def _statistics_app(tmp_path: Path):
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    prepared = prepare_batch(batch_request(spans=[span_payload()]))
    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.aggregate(BASE_TIME + timedelta(days=1))
    collector = TelemetryCollector(database.open_writer)
    collector.start()
    app = FastAPI()
    app.state.telemetry_reader = database.reader()
    app.state.telemetry_collector = collector
    app.include_router(router, prefix="/api/statistics")
    return app, collector


def test_telemetry_statistics_uses_common_metadata_and_aggregate_only(
    tmp_path: Path,
) -> None:
    app, collector = _statistics_app(tmp_path)

    response = TestClient(app).get(
        "/api/statistics/telemetry",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
            "resolution": "hour",
        },
    )
    collector.close(timeout_seconds=1.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["window_start"] == "2026-08-03T00:00:00Z"
    assert body["data"]["window_end"] == "2026-08-04T00:00:00Z"
    assert body["data"]["timezone"] == "UTC"
    assert body["data"]["sample_size"] == 1
    assert body["data"]["quality"] == "reliable"
    assert body["data"]["freshness"]["telemetry"]["status"] == "fresh"
    assert body["data"]["spans"][0]["histogram_counts"]
    assert "attributes_json" not in str(body)


def test_statistics_query_rejects_bad_range_with_error_envelope(tmp_path: Path) -> None:
    app, collector = _statistics_app(tmp_path)

    response = TestClient(app).get(
        "/api/statistics/telemetry",
        params={
            "start_date": "2026-08-04",
            "end_date": "2026-08-03",
            "timezone": "UTC",
            "resolution": "hour",
        },
    )
    collector.close(timeout_seconds=1.0)

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["data"] is None


def test_statistics_query_reports_unavailable_dependencies() -> None:
    app = FastAPI()
    app.state.telemetry_reader = None
    app.state.telemetry_collector = None
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/telemetry",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
            "resolution": "day",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"] == "statistics telemetry source unavailable"


def test_agent_statistics_uses_injected_reader_and_common_metadata() -> None:
    app = FastAPI()
    app.state.agent_statistics_reader = EmptyAgentReader()
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/agent",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["sample_size"] == 0
    assert body["data"]["quality"] == "unavailable"
    assert body["data"]["window_start"] == "2026-08-03T00:00:00+08:00"
    assert body["data"]["statuses"] == {
        "completed": 0,
        "running": 0,
        "interrupted": 0,
        "failed": 0,
        "unknown": 0,
    }


def test_agent_statistics_reports_unavailable_reader() -> None:
    app = FastAPI()
    app.state.agent_statistics_reader = None
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/agent",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"] == "statistics agent source unavailable"
