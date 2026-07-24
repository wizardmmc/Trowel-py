from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.quota import routes as quota_routes
from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)


def _app_with(model: QuotaReadModel | None) -> FastAPI:
    app = FastAPI()
    app.state.quota_read_model = model
    app.include_router(quota_routes.router)
    return app


def test_list_quota_returns_normalized_snapshots_without_raw() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(
        QuotaSnapshot(
            provider=Provider.GLM,
            account_id="glm-a",
            plan_level="max",
            windows=(
                QuotaWindow(
                    kind=QuotaWindowKind.WEEKLY,
                    used_percent=90.0,
                    resets_at=1784858417972,
                    raw={"secret": "should-not-leak"},
                ),
            ),
            fetched_at=1000,
            status=QuotaStatus.OK,
        )
    )
    client = TestClient(_app_with(rm))
    resp = client.get("/api/quota")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) == 1
    snap = body["data"][0]
    assert snap["provider"] == "glm"
    assert snap["account_id"] == "glm-a"
    assert snap["plan_level"] == "max"
    assert snap["status"] == "ok"
    window = snap["windows"][0]
    assert window["kind"] == "weekly"
    assert window["used_percent"] == 90.0
    assert window["resets_at"] == 1784858417972
    assert "raw" not in window


def test_list_quota_empty_when_read_model_absent() -> None:
    app = FastAPI()
    app.include_router(quota_routes.router)
    client = TestClient(app)

    resp = client.get("/api/quota")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "data": [], "error": None}
