"""slice-049 profile routes tests (TDD RED -> GREEN).

GET/PUT /api/profile: envelope shape, cold-start empty, PUT->GET round-trip,
updated/source stamping, write-back through the store (C-5), snapshot
insurance on overwrite (C-4 via store), and the 422 error branch.

Isolation: ``dependency_overrides[get_profile_store]`` repoints the store at a
``tmp_path`` memory root, so the real ``~/.trowel/memory`` is never touched.
"""
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
    """TestClient wired to an isolated tmp_path memory root."""
    store = MemoryStore(tmp_path)
    app = create_app()
    app.dependency_overrides[get_profile_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- envelope shape ----


def test_get_envelope_shape(client: TestClient) -> None:
    resp = client.get("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is True
    assert body["error"] is None


# ---- cold start: no profile.md ----


def test_get_empty_when_no_file(client: TestClient) -> None:
    """cold start: GET returns empty five dims, not an error (C-6 of 047)."""
    resp = client.get("/api/profile")
    data = resp.json()["data"]
    assert data["ability"] == ""
    assert data["methodology"] == ""
    assert data["expression"] == ""
    assert data["goal"] == ""
    assert data["other"] == ""
    assert data["source"] == "user-edit"  # empty_profile default


# ---- PUT -> GET round-trip ----


def test_put_then_get_roundtrip(client: TestClient) -> None:
    payload = {
        "ability": "网安硕士 / 红队",
        "methodology": "spec-first，spike 实测",
        "expression": "大白话，禁翻译腔",
        "goal": "反诈论文 + trowel",
        "other": "在啃保形预测",
    }
    resp = client.put("/api/profile", json=payload)
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    data = client.get("/api/profile").json()["data"]
    assert data["ability"] == "网安硕士 / 红队"
    assert data["methodology"] == "spec-first，spike 实测"
    assert data["expression"] == "大白话，禁翻译腔"
    assert data["goal"] == "反诈论文 + trowel"
    assert data["other"] == "在啃保形预测"


def test_put_returns_dto_with_updated_and_source(client: TestClient) -> None:
    """PUT response carries server-stamped updated + source (not from body)."""
    resp = client.put("/api/profile", json={"ability": "x"})
    data = resp.json()["data"]
    assert data["updated"] == date.today().isoformat()
    assert data["source"] == "user-edit"


def test_put_writes_back_through_store(client: TestClient, tmp_path: Path) -> None:
    """PUT must persist via store.write_profile (C-5), not bypass it."""
    client.put("/api/profile", json={"ability": "persisted"})
    assert (tmp_path / "profile.md").exists()
    # a fresh store re-reads the same content the HTTP layer wrote
    fresh = MemoryStore(tmp_path).load_profile()
    assert fresh.ability == "persisted"


def test_put_partial_dims_default_empty(client: TestClient) -> None:
    """omitted dims default to empty string (ProfileUpdate defaults)."""
    resp = client.put("/api/profile", json={"ability": "only this"})
    data = resp.json()["data"]
    assert data["ability"] == "only this"
    assert data["methodology"] == ""
    assert data["other"] == ""


def test_put_overwrite_snapshots_prior(client: TestClient, tmp_path: Path) -> None:
    """second PUT overwrites; C-4 snapshot of the prior version lands on disk."""
    client.put("/api/profile", json={"ability": "v1"})
    client.put("/api/profile", json={"ability": "v2"})
    assert client.get("/api/profile").json()["data"]["ability"] == "v2"
    hist = tmp_path / "meta" / "profile-history"
    assert hist.exists()
    assert any("v1" in p.read_text(encoding="utf-8") for p in hist.glob("*.md"))


# ---- error branch ----


def test_put_invalid_type_rejected(client: TestClient) -> None:
    """non-string dim -> FastAPI 422 before the handler runs."""
    resp = client.put("/api/profile", json={"ability": 123})
    assert resp.status_code == 422
