from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

import pytest

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.capacity import CapacityLimits
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


def test_create_cc_session_separates_checkpoint_support_from_availability(
    hub: SessionHub,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime 支持 checkpoint 时，单个非 Git 工作目录仍必须标记为不可用。"""

    monkeypatch.setattr(
        "trowel_py.agent_host.hub.checkpoint.is_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "trowel_py.agent_host.hub.checkpoint.is_git_repo",
        lambda _workdir: False,
    )

    binding = hub.create(cc_req(workdir))

    assert "checkpoint" in binding.capabilities
    assert binding.checkpoint_available is False


def test_create_codex_session_creates_binding_and_registers_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir))
    assert binding.runtime is Runtime.CODEX
    assert binding.session_id in codex_mgr.sessions
    assert hub.get(binding.session_id) is not None


def test_create_cc_rolls_back_runtime_when_binding_write_fails(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_put(_binding) -> None:
        raise OSError("binding store unavailable")

    monkeypatch.setattr(hub.store, "put", fail_put)

    with pytest.raises(OSError, match="binding store unavailable"):
        hub.create(cc_req(workdir))

    assert cc_registry == {}


def test_create_codex_rolls_back_runtime_when_binding_write_fails(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_put(_binding) -> None:
        raise OSError("binding store unavailable")

    monkeypatch.setattr(hub.store, "put", fail_put)

    with pytest.raises(OSError, match="binding store unavailable"):
        hub.create(codex_req(workdir))

    assert codex_mgr.sessions == {}


@pytest.mark.parametrize("runtime", [Runtime.CLAUDE_CODE, Runtime.CODEX])
def test_create_rolls_back_binding_and_runtime_when_delegate_index_write_fails(
    runtime: Runtime,
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_add(_runtime, _native_session_id) -> None:
        raise OSError("delegate index unavailable")

    monkeypatch.setattr(hub._non_user_identities, "add", fail_add)
    request = (
        cc_req(workdir, session_kind="delegate", resume_from="native-session")
        if runtime is Runtime.CLAUDE_CODE
        else codex_req(workdir, session_kind="delegate", resume_from="native-session")
    )

    with pytest.raises(OSError, match="delegate index unavailable"):
        hub.create(request)

    assert hub.store.list_all() == []
    assert cc_registry == {}
    assert codex_mgr.sessions == {}


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
        "display_name": "project",
    }


def test_create_missing_workdir_400(hub: SessionHub):
    with pytest.raises(InvalidSessionRequestError, match="workdir does not exist"):
        hub.create(cc_req(Path("/nonexistent/xyz-123")))


def test_create_connection_cap_409(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
) -> None:
    hub = hub_factory(
        CapacityLimits(
            user_connections=1,
            delegate_connections=5,
            delegate_running=5,
        )
    )
    hub.create(cc_req(workdir))
    with pytest.raises(SessionConflictError, match="连接数已达上限"):
        hub.create(codex_req(workdir))
