"""画像读写与建议审核路由测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.memory.store import MemoryStore
from trowel_py.profile.service import get_profile_store


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """把画像仓储替换到临时目录。"""
    store = MemoryStore(tmp_path)
    app = create_app()
    app.dependency_overrides[get_profile_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_envelope_shape(client: TestClient) -> None:
    resp = client.get("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is True
    assert body["error"] is None


def test_get_empty_when_no_file(client: TestClient) -> None:
    resp = client.get("/api/profile")
    data = resp.json()["data"]
    assert data["ability"] == ""
    assert data["methodology"] == ""
    assert data["expression"] == ""
    assert data["goal"] == ""
    assert data["other"] == ""
    assert data["source"] == "user-edit"


def test_put_then_get_roundtrip(client: TestClient) -> None:
    payload = {
        "ability": "熟悉 Python / 并发调试",
        "methodology": "先写契约，再做小步验证",
        "expression": "简洁直接",
        "goal": "持续改进项目可靠性",
        "other": "关注可观测性",
    }
    resp = client.put("/api/profile", json=payload)
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    data = client.get("/api/profile").json()["data"]
    assert data["ability"] == "熟悉 Python / 并发调试"
    assert data["methodology"] == "先写契约，再做小步验证"
    assert data["expression"] == "简洁直接"
    assert data["goal"] == "持续改进项目可靠性"
    assert data["other"] == "关注可观测性"


def test_put_returns_dto_with_updated_and_source(client: TestClient) -> None:
    """更新时间与来源由服务端写入。"""
    resp = client.put("/api/profile", json={"ability": "x"})
    data = resp.json()["data"]
    assert data["updated"] == date.today().isoformat()
    assert data["source"] == "user-edit"


def test_put_writes_back_through_store(client: TestClient, tmp_path: Path) -> None:
    client.put("/api/profile", json={"ability": "persisted"})
    assert (tmp_path / "profile.md").exists()
    fresh = MemoryStore(tmp_path).load_profile()
    assert fresh.ability == "persisted"


def test_put_partial_dims_default_empty(client: TestClient) -> None:
    resp = client.put("/api/profile", json={"ability": "only this"})
    data = resp.json()["data"]
    assert data["ability"] == "only this"
    assert data["methodology"] == ""
    assert data["other"] == ""


def test_put_overwrite_snapshots_prior(client: TestClient, tmp_path: Path) -> None:
    client.put("/api/profile", json={"ability": "v1"})
    client.put("/api/profile", json={"ability": "v2"})
    assert client.get("/api/profile").json()["data"]["ability"] == "v2"
    hist = tmp_path / "meta" / "profile-history"
    assert hist.exists()
    assert any("v1" in p.read_text(encoding="utf-8") for p in hist.glob("*.md"))


def test_put_invalid_type_rejected(client: TestClient) -> None:
    resp = client.put("/api/profile", json={"ability": 123})
    assert resp.status_code == 422


def _seed_suggestion(
    tmp_path: Path,
    id_: str = "s1",
    dimension: str = "ability",
    body: str = "会 FastAPI",
    status: str = "pending",
) -> None:
    from trowel_py.memory.profile_suggestions import (
        PROFILE_DISTILL_POLICY_VERSION,
        append_suggestions,
    )
    from trowel_py.memory.types import Suggestion

    append_suggestions(
        tmp_path,
        [
            Suggestion(
                id=id_,
                dimension=dimension,  # type: ignore[arg-type]
                body=body,
                sources=("sess-abc",),
                date="2026-07-14",
                status=status,  # type: ignore[arg-type]
                policy_version=PROFILE_DISTILL_POLICY_VERSION,
            )
        ],
        updated="2026-07-15",
    )


def test_get_suggestions_empty(client: TestClient) -> None:
    resp = client.get("/api/profile/suggestions")
    assert resp.json() == {"success": True, "data": [], "error": None}


def test_get_suggestions_returns_only_pending(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_suggestion(tmp_path, "s1", body="会 FastAPI")
    _seed_suggestion(
        tmp_path,
        "s2",
        body="提升服务可靠性",
        status="accepted",
    )
    data = client.get("/api/profile/suggestions").json()["data"]
    assert [s["id"] for s in data] == ["s1"]
    assert data[0]["body"] == "会 FastAPI"
    assert data[0]["sources"] == ["sess-abc"]
    assert data[0]["dimension"] == "ability"


def test_patch_suggestion_accept(client: TestClient, tmp_path: Path) -> None:
    _seed_suggestion(tmp_path, "s1")
    resp = client.patch("/api/profile/suggestions/s1", json={"status": "accepted"})
    assert resp.json()["success"] is True
    assert client.get("/api/profile/suggestions").json()["data"] == []


def test_patch_suggestion_discard(client: TestClient, tmp_path: Path) -> None:
    _seed_suggestion(tmp_path, "s1")
    resp = client.patch("/api/profile/suggestions/s1", json={"status": "discarded"})
    assert resp.json()["success"] is True
    assert client.get("/api/profile/suggestions").json()["data"] == []


def test_patch_suggestion_unknown_id(client: TestClient) -> None:
    resp = client.patch("/api/profile/suggestions/nope", json={"status": "accepted"})
    body = resp.json()
    assert body["success"] is False
    assert "not found" in body["error"]


def test_patch_suggestion_bad_status_422(client: TestClient, tmp_path: Path) -> None:
    _seed_suggestion(tmp_path, "s1")
    resp = client.patch("/api/profile/suggestions/s1", json={"status": "frozen"})
    assert resp.status_code == 422


def test_put_with_ai_calibration_source(client: TestClient) -> None:
    resp = client.put(
        "/api/profile", json={"ability": "from AI", "source": "ai-calibration"}
    )
    assert resp.json()["data"]["source"] == "ai-calibration"
