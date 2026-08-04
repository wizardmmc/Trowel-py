"""验证遥测写入口的认证、部分接受和故障 envelope。"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.telemetry.support import batch_request, span_payload
from trowel_py.desktop.access import DesktopCredentialMiddleware
from trowel_py.telemetry.collector import TelemetryCollector
from trowel_py.telemetry.routes import router
from trowel_py.telemetry.storage import TelemetryDatabase


def _app(tmp_path: Path, *, credential: str | None = None):
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    collector = TelemetryCollector(
        database.open_writer,
        flush_interval_seconds=0.01,
        maintenance_interval_seconds=3600,
    )
    collector.start()
    app = FastAPI()
    if credential is not None:
        app.add_middleware(DesktopCredentialMiddleware, credential=credential)
    app.state.telemetry_collector = collector
    app.include_router(router, prefix="/api/telemetry")
    return app, database, collector


def test_batch_endpoint_partially_accepts_records_and_reports_categories(
    tmp_path: Path,
) -> None:
    app, database, collector = _app(tmp_path)
    private = span_payload(2)
    private["tool_result"] = "private result"
    body = batch_request(spans=[span_payload(1), private]).model_dump(mode="json")

    response = TestClient(app).post("/api/telemetry/batches", json=body)
    collector.close(timeout_seconds=1.0)

    assert response.status_code == 202
    assert response.json() == {
        "success": True,
        "data": {
            "accepted": 1,
            "rejected": 1,
            "dropped": 0,
            "duplicate": False,
            "error_categories": {"privacy_field": 1},
        },
        "error": None,
    }
    assert database.reader().raw_counts()["spans"] == 1


def test_batch_endpoint_uses_existing_desktop_instance_authentication(
    tmp_path: Path,
) -> None:
    app, _database, collector = _app(tmp_path, credential="desktop-secret")
    body = batch_request().model_dump(mode="json")
    client = TestClient(app)

    denied = client.post("/api/telemetry/batches", json=body)
    accepted = client.post(
        "/api/telemetry/batches",
        json=body,
        headers={"Authorization": "Bearer desktop-secret"},
    )
    collector.close(timeout_seconds=1.0)

    assert denied.status_code == 401
    assert denied.json()["success"] is False
    assert accepted.status_code == 202


def test_batch_endpoint_returns_unavailable_envelope_without_collector() -> None:
    app = FastAPI()
    app.state.telemetry_collector = None
    app.include_router(router, prefix="/api/telemetry")

    response = TestClient(app).post(
        "/api/telemetry/batches",
        json=batch_request().model_dump(mode="json"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "data": None,
        "error": "telemetry collector unavailable",
    }


def test_duplicate_endpoint_retry_is_explicit(tmp_path: Path) -> None:
    app, _database, collector = _app(tmp_path)
    body = batch_request().model_dump(mode="json")
    client = TestClient(app)

    first = client.post("/api/telemetry/batches", json=body)
    duplicate = client.post("/api/telemetry/batches", json=body)
    time.sleep(0.03)
    collector.close(timeout_seconds=1.0)

    assert first.json()["data"]["accepted"] == 1
    assert duplicate.json()["data"]["duplicate"] is True
    assert duplicate.json()["data"]["accepted"] == 0
