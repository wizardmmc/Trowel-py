from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.hub import (
    InvalidSessionRequestError,
    SessionHub,
    SessionConflictError,
)
from trowel_py.agent_host.store import BindingStore
from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    cc_req,
    codex_req,
    make_cc_opener,
)


def test_create_cc_session_creates_binding_and_registry_host(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir))
    assert binding.runtime is Runtime.CLAUDE_CODE
    assert binding.workdir == str(workdir)
    assert binding.session_id in cc_registry
    assert cc_registry[binding.session_id].workdir == str(workdir)
    assert hub.get(binding.session_id) is not None


def test_create_codex_session_creates_binding_and_registers_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir))
    assert binding.runtime is Runtime.CODEX
    assert binding.session_id in codex_mgr.sessions
    assert hub.get(binding.session_id) is not None


def test_create_cc_passes_only_explicit_launch_configuration(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    registry: dict[str, FakeCcHost] = {}
    configured = make_cc_opener(registry, {})
    seen: dict[str, object] = {}

    def opener(req, target_registry, **launch_config):
        seen.update(launch_config)
        return configured(req, target_registry, **launch_config)

    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        cc_registry=registry,
        cc_opener=opener,
        cc_proxy_base_url="http://127.0.0.1:8123",
        cc_settings_path=tmp_path / "settings.json",
        codex_config_home=tmp_path,
    )

    hub.create(cc_req(workdir))

    assert seen == {
        "proxy_base_url": "http://127.0.0.1:8123",
        "settings_path": tmp_path / "settings.json",
    }


def test_create_missing_workdir_400(hub: SessionHub):
    with pytest.raises(InvalidSessionRequestError, match="workdir does not exist"):
        hub.create(cc_req(Path("/nonexistent/xyz-123")))


def test_create_connection_cap_409(hub: SessionHub, workdir: Path, monkeypatch):
    monkeypatch.setattr("trowel_py.agent_host.hub.MAX_CONNECTIONS", 1)
    hub.create(cc_req(workdir))
    with pytest.raises(SessionConflictError, match="连接数已达上限"):
        hub.create(codex_req(workdir))
