from collections.abc import Iterator
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.agent_host.hub._support import FakeCodexManager, make_cc_opener
from trowel_py.agent_host.capacity import CapacityLimits
from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.routes import get_hub, router
from trowel_py.agent_host.store import BindingStore


@pytest.fixture
def hub_factory(
    tmp_path: Path,
) -> Callable[[CapacityLimits | None], SessionHub]:
    def build(limits: CapacityLimits | None = None) -> SessionHub:
        """构造使用隔离 binding 和 Codex 配置的路由测试 Hub。"""

        store = BindingStore(tmp_path / "agent_sessions.json")
        cc_registry: dict[str, Any] = {}
        return SessionHub(
            store,
            codex_manager=FakeCodexManager(),
            cc_registry=cc_registry,
            cc_opener=make_cc_opener(cc_registry, {}),
            # 指向临时目录，避免读取开发者真实的 Codex 配置。
            codex_config_home=tmp_path,
            codex_history_root=tmp_path / "memory",
            capacity_limits=limits,
        )

    return build


@pytest.fixture
def hub(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
) -> SessionHub:
    return hub_factory(None)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def client_factory() -> Callable[[SessionHub], TestClient]:
    def build(hub: SessionHub) -> TestClient:
        """把指定 Hub 注入只包含 Agent 路由的测试应用。"""

        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        app.dependency_overrides[get_hub] = lambda: hub
        return TestClient(app)

    return build


@pytest.fixture
def client(
    hub: SessionHub,
    client_factory: Callable[[SessionHub], TestClient],
) -> Iterator[TestClient]:
    with client_factory(hub) as test_client:
        yield test_client
