from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.agent_host.hub._support import FakeCodexManager, make_cc_opener
from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.routes import get_hub, router
from trowel_py.agent_host.store import BindingStore


@pytest.fixture
def hub(tmp_path: Path) -> SessionHub:
    store = BindingStore(tmp_path / "agent_sessions.json")
    cc_registry: dict[str, Any] = {}
    return SessionHub(
        store,
        codex_manager=FakeCodexManager(),
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, {}),
        # 指向临时目录，避免读取开发者真实的 Codex 配置。
        codex_config_home=tmp_path,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def client(hub: SessionHub) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router, prefix="/api/agent")
    app.dependency_overrides[get_hub] = lambda: hub
    with TestClient(app) as test_client:
        yield test_client
