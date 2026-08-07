from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.capacity import CapacityLimits
from trowel_py.agent_host.configuration_archive import SessionConfigurationArchive
from trowel_py.agent_host.hub import (
    ConditionMismatchError,
    FrozenConnectionExpectation,
    InvalidSessionRequestError,
    SessionHub,
    SessionConflictError,
)
from trowel_py.agent_host.store import BindingStore
from trowel_py.configuration.models import (
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
)
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
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


def _connection_launch(runtime: RuntimeKind) -> RuntimeLaunchConfiguration:
    """构造 Agent Hub 测试使用的已验证冻结连接。"""

    is_claude = runtime is RuntimeKind.CLAUDE_CODE
    return RuntimeLaunchConfiguration(
        connection_id="connection-a",
        connection_version=4,
        connection_identity_version=3,
        connection_name="Provider A",
        runtime=runtime,
        kind=(
            ConnectionKind.CLAUDE_COMPATIBLE
            if is_claude
            else ConnectionKind.CODEX_CUSTOM
        ),
        protocol=(
            ProtocolKind.ANTHROPIC_MESSAGES
            if is_claude
            else ProtocolKind.OPENAI_RESPONSES
        ),
        model="glm-5.2" if is_claude else "deepseek-v4-flash",
        effort=None if is_claude else "high",
        capability_version="provider-runtime-capabilities-v1",
        base_url="https://provider.example/v1",
        login_directory=None,
        proxy_url=None,
        claude_role_models={"default": "glm-5.2"} if is_claude else {},
        codex_catalog=(),
        api_key="private-key",
    )


def test_create_cc_freezes_connection_and_uses_private_proxy_path(
    tmp_path: Path,
) -> None:
    """连接级 Claude 会话只把脱敏身份写入 binding。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    registry: dict[str, FakeCcHost] = {}
    configured = make_cc_opener(registry, {})
    seen: dict[str, object] = {}

    class ProxyRegistry:
        """返回稳定测试租约并记录释放。"""

        released = False

        def acquire(self, upstream: str, proxy_url: str | None = None) -> str:
            assert upstream == "https://provider.example/v1"
            assert proxy_url is None
            return "opaque-lease"

        def release(self, token: str) -> None:
            assert token == "opaque-lease"
            self.released = True

    proxy_registry = ProxyRegistry()

    def opener(req, target_registry, **launch_config):
        seen.update(launch_config)
        compatible = dict(launch_config)
        compatible.pop("owned_settings_path")
        compatible.pop("close_callback")
        compatible.pop("memory_mcp_enabled")
        compatible.pop("claude_config_dir")
        compatible.pop("claude_plugin_dir")
        return configured(req, target_registry, **compatible)

    launch = replace(
        _connection_launch(RuntimeKind.CLAUDE_CODE),
        claude_config_dir=str(tmp_path / "connection-home"),
        claude_plugin_dir=str(tmp_path / "global-plugins"),
    )
    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        cc_registry=registry,
        cc_opener=opener,
        cc_proxy_base_url="http://127.0.0.1:8123",
        configuration_resolver=lambda *_args: launch,
        cc_connection_proxy_registry=proxy_registry,
        codex_config_home=tmp_path,
    )

    binding = hub.create(
        cc_req(
            workdir,
            connection_id=launch.connection_id,
            model=launch.model,
            memory_enabled=False,
            agent_mcp_enabled=False,
        )
    )

    assert binding.connection_id == launch.connection_id
    assert binding.connection_name == "Provider A"
    assert seen["proxy_base_url"].endswith("/api/cc-runtime/opaque-lease")
    assert seen["claude_config_dir"] == launch.claude_config_dir
    assert seen["claude_plugin_dir"] == launch.claude_plugin_dir
    settings_path = Path(seen["settings_path"])
    assert settings_path.is_file()
    assert "private-key" not in repr(binding)
    settings_path.unlink()
    seen["close_callback"]()
    assert proxy_registry.released is True


def test_resume_cc_keeps_archived_connection_home(tmp_path: Path) -> None:
    """同一连接后续分配了新目录时，旧原生会话仍必须回到创建时的家。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    archive = SessionConfigurationArchive(tmp_path / "native-configurations.json")
    frozen = make_binding(
        session_id="closed-session",
        runtime=Runtime.CLAUDE_CODE,
        native_session_id="claude-native-a",
        workdir=str(workdir),
        model="glm-5.2",
        effort="high",
        permission="bypassPermissions",
        memory_enabled=False,
        memory_mcp_enabled=False,
        profile_enabled=True,
        capabilities=("streaming",),
        name="project",
        connection_id="connection-a",
        connection_identity_version=3,
        connection_name="Provider A",
        connection_kind="claude_compatible",
        agent_mcp_enabled=False,
    )
    original_home = tmp_path / "original-connection-home"
    archive.put(frozen, claude_config_dir=original_home)
    current_launch = replace(
        _connection_launch(RuntimeKind.CLAUDE_CODE),
        claude_config_dir=str(tmp_path / "newly-allocated-home"),
        claude_plugin_dir=str(tmp_path / "global-plugins"),
    )
    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        cc_registry={},
        configuration_archive=archive,
        configuration_resolver=lambda *_args: current_launch,
    )

    prepared = hub._inherit_resume_config(
        cc_req(workdir, resume_from="claude-native-a")
    )
    resolved = hub._resolve_launch(prepared)

    assert resolved is not None
    assert resolved.claude_config_dir == str(original_home.resolve())
    assert current_launch.claude_config_dir != resolved.claude_config_dir


def test_discussion_frozen_connection_preflight_runs_before_runtime_create(
    tmp_path: Path,
) -> None:
    """连接身份漂移时不能先用新凭据接触 participant 的冻结原生历史。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    launch = _connection_launch(RuntimeKind.CLAUDE_CODE)
    opener_called = False

    def forbidden_opener(*args, **kwargs):
        """若 preflight 失效，记录越过连接边界并让测试失败。"""

        nonlocal opener_called
        del args, kwargs
        opener_called = True
        raise AssertionError("runtime create must not run before frozen preflight")

    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        cc_registry={},
        cc_opener=forbidden_opener,
        cc_proxy_base_url="http://127.0.0.1:8123",
        configuration_resolver=lambda *_args: launch,
        codex_config_home=tmp_path,
    )

    with pytest.raises(ConditionMismatchError, match="identity or capability changed"):
        hub.create(
            cc_req(
                workdir,
                connection_id=launch.connection_id,
                model=launch.model,
                memory_enabled=False,
                profile_enabled=False,
                self_enabled=False,
                session_kind="discussion",
                memory_eligibility=False,
                agent_mcp_enabled=False,
                owner_ref="discussion:preflight:participant:0:v1",
            ),
            frozen_connection=FrozenConnectionExpectation(
                connection_identity_version=launch.connection_identity_version + 1,
                capability_version=launch.capability_version,
                capability_source=launch.capability_source,
            ),
        )

    assert opener_called is False


def test_create_codex_registers_session_in_selected_connection_pool(
    tmp_path: Path,
) -> None:
    """Codex 会话注册时必须把冻结连接传给 manager pool。"""

    class Pool(FakeCodexManager):
        def __init__(self) -> None:
            super().__init__()
            self.launches: list[RuntimeLaunchConfiguration] = []

        def register(self, session, *, launch=None) -> None:
            super().register(session)
            if launch is not None:
                self.launches.append(launch)

    workdir = tmp_path / "project"
    workdir.mkdir()
    manager = Pool()
    launch = _connection_launch(RuntimeKind.CODEX)
    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        codex_manager=manager,
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: launch,
    )

    binding = hub.create(
        codex_req(
            workdir,
            connection_id=launch.connection_id,
            model=launch.model,
            effort=launch.effort,
            memory_enabled=False,
            agent_mcp_enabled=False,
        )
    )

    assert manager.launches == [launch]
    assert binding.connection_identity_version == 3


def test_connection_session_rejects_unverified_agent_mcp(tmp_path: Path) -> None:
    """连接级 MCP 未经真实 Gate 时，API 不能绕过前端强行开启。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    launch = _connection_launch(RuntimeKind.CODEX)
    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        codex_manager=FakeCodexManager(),
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: launch,
    )

    with pytest.raises(InvalidSessionRequestError, match="MCP is not verified"):
        hub.create(
            codex_req(
                workdir,
                connection_id=launch.connection_id,
                model=launch.model,
                effort=launch.effort,
                memory_enabled=False,
                agent_mcp_enabled=True,
            )
        )


def test_connection_session_keeps_memory_text_but_closes_unverified_mcp(
    tmp_path: Path,
) -> None:
    """连接会话保留 Memory 正文，同时不挂载尚未验证的 Memory MCP。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    launch = _connection_launch(RuntimeKind.CODEX)
    manager = FakeCodexManager()
    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        codex_manager=manager,
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: launch,
    )

    binding = hub.create(
        codex_req(
            workdir,
            connection_id=launch.connection_id,
            model=launch.model,
            effort=launch.effort,
            memory_enabled=True,
            agent_mcp_enabled=False,
        )
    )

    session = manager.get_session(binding.session_id)
    assert binding.memory_enabled is True
    assert binding.memory_mcp_enabled is False
    assert session.config.developer_instructions is not None
    assert session.config.trowel_memory_mcp is None


def test_connection_requirement_is_evaluated_for_each_create(tmp_path: Path) -> None:
    """应用启动后新增首个连接，也必须立刻关闭无连接创建旁路。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    strict = False
    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        cc_registry={},
        cc_opener=make_cc_opener({}, {}),
        codex_config_home=tmp_path,
        require_configured_connections=lambda: strict,
    )

    hub.create(cc_req(workdir))
    strict = True
    with pytest.raises(InvalidSessionRequestError, match="必须选择"):
        hub.create(cc_req(workdir))


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
