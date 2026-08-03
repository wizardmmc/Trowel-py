"""验证 Statistics API 的统一时间窗、质量和新鲜度响应。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.telemetry.support import (
    BASE_TIME,
    batch_request,
    metric_payload,
    span_payload,
)
from trowel_py.statistics.routes import router
from trowel_py.statistics.calls.repository import CallStatisticsReader
from trowel_py.statistics.memory.repository import FileMemoryStatisticsReader
from trowel_py.statistics.runtime.repository import RuntimeStatisticsReader
from trowel_py.telemetry.collector import TelemetryCollector
from trowel_py.telemetry.contracts import prepare_batch
from trowel_py.telemetry.storage import TelemetryDatabase


class EmptyAgentReader:
    """返回没有用户 session 的 Agent 统计来源。"""

    def read(self, _window):
        """返回空 session 集合。"""

        return []


def _statistics_app(tmp_path: Path, *, include_runtime_metric: bool = False):
    """创建只使用临时 telemetry.db 的 Statistics 测试应用。

    Args:
        tmp_path: pytest 为当前用例分配的隔离目录。
        include_runtime_metric: 是否加入用于验证最新残留值的 runtime gauge。
    """

    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    remaining_metric = metric_payload()
    remaining_metric.update(
        {
            "component": "runtime",
            "name": "resource.remaining",
            "kind": "gauge",
            "value": 2,
            "operation": "resource.session.close",
        }
    )
    prepared = prepare_batch(
        batch_request(
            spans=[span_payload()],
            metrics=[remaining_metric] if include_runtime_metric else [],
        )
    )
    with database.open_writer() as writer:
        writer.write_batches([prepared])
        writer.aggregate(BASE_TIME + timedelta(days=1))
    collector = TelemetryCollector(database.open_writer)
    collector.start()
    app = FastAPI()
    app.state.telemetry_reader = database.reader()
    app.state.telemetry_collector = collector
    app.state.call_statistics_reader = CallStatisticsReader(database)
    app.state.runtime_statistics_reader = RuntimeStatisticsReader(
        database.reader(),
        {
            "telemetry.db": (database.path, "telemetry"),
        },
    )
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


def test_runtime_statistics_uses_isolated_telemetry_and_hides_paths(
    tmp_path: Path,
) -> None:
    """运行端点只返回受控文件名，不泄露临时数据库绝对路径。"""

    app, collector = _statistics_app(tmp_path, include_runtime_metric=True)

    response = TestClient(app).get(
        "/api/statistics/runtime",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
        },
    )
    collector.close(timeout_seconds=1.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["fastapi"][1]["sample_size"] == 1
    assert [item["name"] for item in body["data"]["sqlite"]["files"]] == [
        "sessions.db",
        "workspaces.db",
        "telemetry.db",
    ]
    assert body["data"]["resource_remaining_count"] == 2
    assert str(tmp_path) not in response.text


def test_calls_list_filters_and_returns_common_envelope(tmp_path: Path) -> None:
    """调用列表只返回白名单字段和稳定下一页游标。"""

    app, collector = _statistics_app(tmp_path)

    response = TestClient(app).get(
        "/api/statistics/calls",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
            "component": "fastapi",
            "operation": "http.statistics.query",
            "status": "ok",
            "minimum_duration_ms": "10",
            "limit": "1",
        },
    )
    collector.close(timeout_seconds=1.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["sample_size"] == 1
    assert body["data"]["items"][0]["component"] == "fastapi"
    assert body["data"]["items"][0]["trace_id"] == f"{1:032x}"
    assert "session_ref" not in response.text
    assert "call_ref" not in response.text
    assert "attributes_json" not in response.text


def test_calls_detail_and_bad_queries_use_error_envelope(tmp_path: Path) -> None:
    """详情、无结果和无效筛选都使用 Statistics 错误 envelope。"""

    app, collector = _statistics_app(tmp_path)
    client = TestClient(app)

    detail = client.get(f"/api/statistics/calls/{1:032x}")
    missing = client.get(f"/api/statistics/calls/{99:032x}")
    invalid_trace = client.get("/api/statistics/calls/not-hex")
    invalid_filter = client.get(
        "/api/statistics/calls",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
            "component": "dynamic-private-value",
        },
    )
    collector.close(timeout_seconds=1.0)

    assert detail.status_code == 200
    assert detail.json()["data"]["root_operation"] == "http.statistics.query"
    for response, status_code in (
        (missing, 404),
        (invalid_trace, 422),
        (invalid_filter, 422),
    ):
        assert response.status_code == status_code
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


def test_memory_statistics_uses_injected_isolated_root(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.memory_statistics_reader = FileMemoryStatisticsReader(tmp_path)
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/memory",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["window_start"] == "2026-08-03T00:00:00+08:00"
    assert body["data"]["sample_size"] == 0
    assert body["data"]["quality"] == "unavailable"
    assert body["data"]["assets"]["active_notes"] == 0


def test_memory_statistics_reports_unavailable_reader() -> None:
    app = FastAPI()
    app.state.memory_statistics_reader = None
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/memory",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"] == "statistics memory source unavailable"
