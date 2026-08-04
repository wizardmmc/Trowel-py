"""验证 Overview HTTP 参数、来源装配和局部降级契约。"""

from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.statistics.overview.service import compose_overview_statistics
from trowel_py.statistics.routes import router
from trowel_py.statistics.window import parse_statistics_window


def test_overview_keeps_all_missing_sources_in_successful_layout() -> None:
    """总览不能因单个领域未装配而让整页 503。"""

    app = FastAPI()
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/overview",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["quality"] == "unavailable"
    assert data["agent"]["tokens"]["total"] is None
    assert len(data["token_trend"]) == 1
    assert all(point["token_total"] is None for point in data["token_trend"])
    assert set(data["sources"]) == {
        "agent",
        "memory",
        "runtime",
        "calls",
        "session_problems",
    }


def test_overview_trend_follows_the_selected_natural_date_range() -> None:
    """总览趋势点数跟随共享日期范围，不再固定最近七天。"""

    app = FastAPI()
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/overview",
        params={
            "start_date": "2026-07-22",
            "end_date": "2026-08-04",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    points = response.json()["data"]["token_trend"]
    assert len(points) == 14
    assert points[0]["date"] == "2026-07-22"
    assert points[-1]["date"] == "2026-08-04"


def test_overview_rejects_invalid_date_window_with_common_envelope() -> None:
    """总览沿用 Statistics 日期校验和错误 envelope。"""

    app = FastAPI()
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/overview",
        params={
            "start_date": "2026-08-04",
            "end_date": "2026-08-03",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "data": None,
        "error": "end_date must not be before start_date",
    }


def test_overview_route_passes_injected_readers_without_opening_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """route 只负责装配 app state 中的 reader，不自行连接真实数据源。"""

    app = FastAPI()
    app.include_router(router, prefix="/api/statistics")
    markers = {
        "agent_statistics_reader": object(),
        "memory_statistics_reader": object(),
        "runtime_statistics_reader": object(),
        "telemetry_collector": object(),
        "call_statistics_reader": object(),
        "session_problem_statistics_reader": object(),
    }
    for name, value in markers.items():
        setattr(app.state, name, value)
    window = parse_statistics_window(
        date(2026, 8, 3),
        date(2026, 8, 3),
        "UTC",
    )
    result = compose_overview_statistics(
        window,
        agent=None,
        daily_agents=(),
        memory=None,
        runtime=None,
        calls=None,
        session_problems=None,
    )
    loader = AsyncMock(return_value=result)
    monkeypatch.setattr(
        "trowel_py.statistics.overview.routes.load_overview_statistics",
        loader,
    )

    response = TestClient(app).get(
        "/api/statistics/overview",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 200
    sources = loader.await_args.args[0]
    assert sources.agent is markers["agent_statistics_reader"]
    assert sources.memory is markers["memory_statistics_reader"]
    assert sources.runtime is markers["runtime_statistics_reader"]
    assert sources.collector is markers["telemetry_collector"]
    assert sources.calls is markers["call_statistics_reader"]
    assert sources.session_problems is markers["session_problem_statistics_reader"]
