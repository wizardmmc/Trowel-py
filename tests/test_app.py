"""Tests for trowel_py.app — SPA fallback + path-traversal guard (slice-027-publish)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app


@pytest.fixture
def web_dist(tmp_path: Path, monkeypatch) -> Path:
    """Mock _find_web_dist to return a tmp dir with an index.html so the SPA
    fallback route registers."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr("trowel_py.app._find_web_dist", lambda: dist)
    return dist


def test_spa_fallback_serves_index_for_unknown_path(web_dist):
    """Non-/api/* GET → index.html so a frontend route refresh survives."""
    client = TestClient(create_app())
    resp = client.get("/cc/some-deep-route")
    assert resp.status_code == 200
    assert b"SPA" in resp.content


def test_spa_fallback_blocks_path_traversal(tmp_path: Path, web_dist):
    """Encoded ../ must NOT escape web_dist — return index.html, not the file.

    Regression guard for the path-traversal CRITICAL: without the resolve +
    relative_to check, GET /%2e%2e/secret.txt would read any file the backend
    user can. The fix confines candidates to web_dist.
    """
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    client = TestClient(create_app())
    resp = client.get("/%2e%2e/secret.txt")
    assert resp.status_code == 200
    assert b"TOPSECRET" not in resp.content  # traversal blocked → index


def test_api_miss_returns_404_not_index(web_dist):
    """/api/unknown must 404 (not fall through to the SPA index)."""
    client = TestClient(create_app())
    resp = client.get("/api/no-such-endpoint")
    assert resp.status_code == 404


def test_health_still_responds(web_dist):
    """Sanity: a registered /api route isn't shadowed by the SPA catch-all."""
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
