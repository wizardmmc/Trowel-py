from pathlib import Path

import pytest

from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.store import BindingStore
from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    make_cc_opener,
)


@pytest.fixture
def cc_registry() -> dict[str, FakeCcHost]:
    return {}


@pytest.fixture
def name_counts() -> dict[str, int]:
    return {}


@pytest.fixture
def codex_mgr() -> FakeCodexManager:
    return FakeCodexManager()


@pytest.fixture
def hub(
    tmp_path: Path,
    cc_registry: dict[str, FakeCcHost],
    name_counts: dict[str, int],
    codex_mgr: FakeCodexManager,
) -> SessionHub:
    store = BindingStore(tmp_path / "agent_sessions.json")
    return SessionHub(
        store,
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
        # 指向临时目录，避免测试读取开发者真实的 Codex 配置。
        codex_config_home=tmp_path,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d
