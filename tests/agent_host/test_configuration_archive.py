"""验证原生会话关闭后仍能按原连接条件恢复。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.configuration_archive import (
    ConfigurationArchiveCorruptError,
    SessionConfigurationArchive,
)
from trowel_py.agent_host.hub import ConditionMismatchError, SessionHub
from trowel_py.agent_host.store import BindingStore
from trowel_py.configuration.models import (
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
)
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
from tests.agent_host.hub._support import FakeCodexManager, codex_req


class _ConnectionPool(FakeCodexManager):
    """记录恢复请求被路由到的冻结连接。"""

    def __init__(self) -> None:
        """初始化普通测试 manager 与启动配置记录。"""

        super().__init__()
        self.launches: list[RuntimeLaunchConfiguration] = []

    def register(
        self,
        session: object,
        *,
        launch: RuntimeLaunchConfiguration | None = None,
    ) -> None:
        """登记会话并保存本次选择的启动配置。"""

        super().register(session)
        if launch is not None:
            self.launches.append(launch)


def _launch(
    identity_version: int = 7,
    *,
    codex_config_dir: str | None = None,
) -> RuntimeLaunchConfiguration:
    """构造不输出真实凭据的已验证 Codex 连接。"""

    return RuntimeLaunchConfiguration(
        connection_id="codex-deepseek",
        connection_version=9,
        connection_identity_version=identity_version,
        connection_name="DeepSeek",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        model="deepseek-v4-flash",
        effort="high",
        capability_version="provider-runtime-capabilities-v1",
        base_url="https://provider.example/v1",
        login_directory=None,
        proxy_url=None,
        claude_role_models={},
        codex_catalog=(),
        codex_config_dir=codex_config_dir,
        api_key="must-not-be-persisted",
    )


def _frozen_binding(workdir: Path):
    """构造已经取得原生 thread ID 的连接型 binding。"""

    return make_binding(
        session_id="closed-session",
        runtime=Runtime.CODEX,
        native_session_id="thread-frozen-connection",
        workdir=str(workdir),
        model="deepseek-v4-flash",
        effort="high",
        permission="Full access · never",
        permission_preset="danger-full-access",
        memory_enabled=False,
        profile_enabled=True,
        self_enabled=True,
        capabilities=("tools",),
        name="project",
        connection_id="codex-deepseek",
        connection_identity_version=7,
        connection_name="DeepSeek",
        connection_kind="codex_custom",
        configuration_capability_version="provider-runtime-capabilities-v1",
        agent_mcp_enabled=False,
    )


def test_archive_is_private_and_contains_no_connection_secret(
    tmp_path: Path,
) -> None:
    """档案只保存恢复条件，不保存 API key 或上游地址。"""

    archive_path = tmp_path / "native-configurations.json"
    archive = SessionConfigurationArchive(archive_path)
    archive.put(_frozen_binding(tmp_path))

    payload = json.loads(archive_path.read_text(encoding="utf-8"))

    assert archive_path.stat().st_mode & 0o777 == 0o600
    assert "must-not-be-persisted" not in archive_path.read_text(encoding="utf-8")
    record = payload["codex:thread-frozen-connection"]
    assert "api_key" not in record
    assert "base_url" not in record


def test_archive_round_trips_claude_auto_memory_condition(tmp_path: Path) -> None:
    """旧 Claude 会话恢复必须沿用创建时冻结的原生记忆条件。"""

    archive = SessionConfigurationArchive(tmp_path / "native-configurations.json")
    binding = make_binding(
        session_id="claude-session",
        runtime=Runtime.CLAUDE_CODE,
        native_session_id="native-claude-session",
        workdir=str(tmp_path),
        model="opus",
        effort="max",
        permission="bypassPermissions",
        memory_enabled=True,
        memory_mcp_enabled=True,
        profile_enabled=True,
        self_enabled=True,
        capabilities=("tools",),
        name="project",
    )

    archive.put(binding, claude_auto_memory_disabled=True)
    restored = archive.get(Runtime.CLAUDE_CODE, "native-claude-session")

    assert restored is not None
    assert restored.claude_auto_memory_disabled is True


def test_live_claude_binding_reads_auto_memory_condition_from_archive(
    tmp_path: Path,
) -> None:
    """binding 尚在时也必须从私有档案恢复不会进入公开 binding 的原生条件。"""

    store = BindingStore(tmp_path / "agent-sessions.json")
    archive = SessionConfigurationArchive(
        tmp_path / "agent-sessions-native-configurations.json"
    )
    binding = make_binding(
        session_id="live-claude-session",
        runtime=Runtime.CLAUDE_CODE,
        native_session_id="live-native-claude-session",
        workdir=str(tmp_path),
        model="opus",
        effort="max",
        permission="bypassPermissions",
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools",),
        name="project",
    )
    store.put(binding)
    archive.put(binding, claude_auto_memory_disabled=True)
    hub = SessionHub(
        store,
        codex_manager=_ConnectionPool(),
        cc_registry={},
        configuration_archive=archive,
    )

    assert (
        hub._resume_claude_auto_memory(
            binding,
            "live-native-claude-session",
            fallback=False,
        )
        is True
    )


def test_corrupt_archive_is_not_overwritten(tmp_path: Path) -> None:
    """整体 JSON 损坏后写入必须失败并保留原文件，不能静默抹掉其他会话。"""

    archive_path = tmp_path / "native-configurations.json"
    original = "{broken archive"
    archive_path.write_text(original, encoding="utf-8")
    archive = SessionConfigurationArchive(archive_path)

    with pytest.raises(ConfigurationArchiveCorruptError):
        archive.put(_frozen_binding(tmp_path))

    assert archive_path.read_text(encoding="utf-8") == original


def test_closed_session_resumes_with_archived_connection_conditions(
    tmp_path: Path,
) -> None:
    """binding 删除后，恢复仍沿用原连接、模型、强度、权限和注入开关。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    store = BindingStore(tmp_path / "agent-sessions.json")
    original = SessionHub(store, codex_manager=_ConnectionPool(), cc_registry={})
    original._configuration_archive.put(_frozen_binding(workdir))
    pool = _ConnectionPool()
    launch = _launch()
    restarted = SessionHub(
        store,
        codex_manager=pool,
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: launch,
        require_configured_connections=True,
    )

    resumed = restarted.create(
        codex_req(workdir, resume_from="thread-frozen-connection")
    )

    assert pool.launches == [launch]
    assert resumed.connection_id == "codex-deepseek"
    assert resumed.model == "deepseek-v4-flash"
    assert resumed.effort == "high"
    assert resumed.permission_preset == "danger-full-access"
    assert resumed.memory_enabled is False
    assert resumed.memory_mcp_enabled is False
    assert resumed.agent_mcp_enabled is False


def test_closed_codex_session_resumes_with_its_archived_config_home(
    tmp_path: Path,
) -> None:
    """恢复旧 thread 时使用创建时配置家，不切到连接当前的新目录。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    archived_home = tmp_path / "accounts" / "archived"
    current_home = tmp_path / "accounts" / "current"
    store = BindingStore(tmp_path / "agent-sessions.json")
    archive = SessionConfigurationArchive(
        tmp_path / "agent-sessions-native-configurations.json"
    )
    archive.put(_frozen_binding(workdir), codex_config_dir=archived_home)
    pool = _ConnectionPool()
    hub = SessionHub(
        store,
        codex_manager=pool,
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: _launch(
            codex_config_dir=str(current_home)
        ),
        configuration_archive=archive,
        require_configured_connections=True,
    )

    hub.create(codex_req(workdir, resume_from="thread-frozen-connection"))

    assert len(pool.launches) == 1
    assert pool.launches[0].codex_config_dir == str(archived_home.resolve())


def test_resume_rejects_connection_identity_changed_since_creation(
    tmp_path: Path,
) -> None:
    """同一连接被重绑后不能静默拿新凭据继续旧 thread。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    store = BindingStore(tmp_path / "agent-sessions.json")
    archive = SessionConfigurationArchive(
        tmp_path / "agent-sessions-native-configurations.json"
    )
    archive.put(_frozen_binding(workdir))
    hub = SessionHub(
        store,
        codex_manager=_ConnectionPool(),
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: _launch(identity_version=8),
        configuration_archive=archive,
        require_configured_connections=True,
    )

    with pytest.raises(ConditionMismatchError, match="manual rebind"):
        hub.create(codex_req(workdir, resume_from="thread-frozen-connection"))


def test_resume_checks_live_binding_identity_without_archive(tmp_path: Path) -> None:
    """binding 尚在时也必须拒绝用重绑后的连接身份恢复。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    store = BindingStore(tmp_path / "agent-sessions.json")
    store.put(_frozen_binding(workdir))
    hub = SessionHub(
        store,
        codex_manager=_ConnectionPool(),
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: _launch(identity_version=8),
        require_configured_connections=True,
    )

    with pytest.raises(ConditionMismatchError, match="manual rebind"):
        hub.create(codex_req(workdir, resume_from="thread-frozen-connection"))


def test_archive_failure_prevents_native_binding_writeback(tmp_path: Path) -> None:
    """档案无法落盘时，binding 不能先写入原生 thread ID 形成半状态。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    store = BindingStore(tmp_path / "agent-sessions.json")
    manager = _ConnectionPool()
    hub = SessionHub(
        store,
        codex_manager=manager,
        cc_registry={},
        codex_config_home=tmp_path,
    )
    created = hub.create(codex_req(workdir))
    session = manager.get_session(created.session_id)
    session.attach_thread_binding(
        {
            "thread": {"id": "thread-new"},
            "model": "gpt-5.6-sol",
            "modelProvider": "openai",
            "cwd": str(workdir),
            "reasoningEffort": "high",
            "sandbox": {"type": "dangerFullAccess", "networkAccess": True},
            "approvalPolicy": "never",
            "activePermissionProfile": {"id": "full-access"},
        }
    )

    def fail_archive(_binding, **_kwargs) -> None:
        raise OSError("archive unavailable")

    hub._configuration_archive.put = fail_archive

    with pytest.raises(OSError, match="archive unavailable"):
        hub._writeback_codex_native(created.session_id, session)

    assert store.get(created.session_id).native_session_id is None


def test_last_choice_retries_after_transient_write_failure(tmp_path: Path) -> None:
    """最近选择写回失败后保留待办，下一次原生写回应继续尝试。"""

    workdir = tmp_path / "project"
    workdir.mkdir()
    store = BindingStore(tmp_path / "agent-sessions.json")
    manager = _ConnectionPool()
    attempts = 0

    def flaky_recorder(_launch_config: RuntimeLaunchConfiguration) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary database lock")

    launch = _launch()
    hub = SessionHub(
        store,
        codex_manager=manager,
        cc_registry={},
        codex_config_home=tmp_path,
        configuration_resolver=lambda *_args: launch,
        last_choice_recorder=flaky_recorder,
    )
    created = hub.create(
        codex_req(
            workdir,
            connection_id=launch.connection_id,
            model=launch.model,
            effort=launch.effort,
            permission_preset="danger-full-access",
            memory_enabled=False,
            agent_mcp_enabled=False,
        )
    )
    session = manager.get_session(created.session_id)
    session.attach_thread_binding(
        {
            "thread": {"id": "thread-choice"},
            "model": launch.model,
            "modelProvider": "deepseek",
            "cwd": str(workdir),
            "reasoningEffort": launch.effort,
            "sandbox": {"type": "dangerFullAccess", "networkAccess": True},
            "approvalPolicy": "never",
        }
    )

    hub._writeback_codex_native(created.session_id, session)
    hub._writeback_codex_native(created.session_id, session)

    assert attempts == 2
    assert created.session_id not in hub._pending_last_choices
