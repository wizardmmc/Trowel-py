"""验证 Recent 工作区的 HTTP 契约和测试数据隔离。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.agent_host.routes import get_workspace_store, router
from trowel_py.agent_host.workspaces import RecentWorkspaceStore


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """创建仅使用临时 Recent 数据库的 Agent 路由客户端。"""

    app = FastAPI()
    store = RecentWorkspaceStore(tmp_path / "workspaces.db")
    app.include_router(router, prefix="/api/agent")
    app.dependency_overrides[get_workspace_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


def test_recent_workspaces_start_empty(client: TestClient) -> None:
    """首次启动时返回空 Recent 列表。"""

    response = client.get("/api/agent/workspaces/recent")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": [],
        "error": None,
    }


def test_remember_workspace_persists_and_moves_duplicate_to_front(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """POST 保存真实目录，重复打开时只移动顺序而不产生副本。"""

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    assert client.post(
        "/api/agent/workspaces/recent", json={"path": str(first)}
    ).status_code == 200
    assert client.post(
        "/api/agent/workspaces/recent", json={"path": str(second)}
    ).status_code == 200
    response = client.post(
        "/api/agent/workspaces/recent", json={"path": str(first)}
    )

    assert response.status_code == 200
    assert response.json()["data"]["path"] == str(first.resolve())
    recent = client.get("/api/agent/workspaces/recent").json()["data"]
    assert [entry["path"] for entry in recent] == [
        str(first.resolve()),
        str(second.resolve()),
    ]
    assert recent[0]["name"] == "first"
    assert recent[0]["available"] is True
    assert recent[0]["last_opened_at"].endswith("+00:00")


def test_remember_workspace_rejects_missing_directory(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """不存在的目录返回 400，且不能进入 Recent。"""

    missing = tmp_path / "missing"

    response = client.post(
        "/api/agent/workspaces/recent", json={"path": str(missing)}
    )

    assert response.status_code == 400
    assert "workspace does not exist" in response.json()["detail"]
    assert client.get("/api/agent/workspaces/recent").json()["data"] == []
