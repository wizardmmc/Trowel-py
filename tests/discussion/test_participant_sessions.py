"""验证 participant binding 的冻结配置与 owner_ref 崩溃恢复。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.store import BindingStore
from trowel_py.discussion.errors import DiscussionRuntimeError
from trowel_py.discussion.models import DiscussionParticipant
from trowel_py.discussion.participant_sessions import AgentHostParticipantSessionAdapter


def _participant(
    *,
    agent_session_id: str | None,
    runtime: Runtime = Runtime.CLAUDE_CODE,
    permission_mode: str | None = "acceptEdits",
    permission_preset: str | None = None,
) -> DiscussionParticipant:
    """构造带 stale SQLite session ID 的最小冻结参与者。"""

    return DiscussionParticipant(
        id="participant-1",
        discussion_id="discussion-1",
        position=0,
        name="glm",
        runtime=runtime,
        connection_id="connection-1",
        connection_name="GLM",
        model="sonnet",
        effective_model="glm-5-test",
        effort=None,
        session_configuration_id="configuration-1",
        permission_mode=permission_mode,
        permission_preset=permission_preset,
        memory_enabled=True,
        profile_enabled=False,
        self_enabled=True,
        connection_identity_version=3,
        owner_ref="discussion:1:participant:0:v1",
        agent_session_id=agent_session_id,
        native_session_id="native-1",
        status="ready",
        capability_version="capability-v1",
        capability_source="真实探针",
        created_at="2026-08-06T00:00:00",
        updated_at="2026-08-06T00:00:00",
    )


def _binding(session_id: str, owner_ref: str):
    """构造 owner 查询测试使用的 discussion binding。"""

    return make_binding(
        session_id=session_id,
        runtime=Runtime.CLAUDE_CODE,
        native_session_id="native-1",
        workdir="/tmp",
        model="sonnet",
        effort=None,
        permission="acceptEdits",
        memory_enabled=True,
        profile_enabled=False,
        self_enabled=True,
        session_kind="discussion",
        memory_eligibility=False,
        agent_mcp_enabled=False,
        owner_ref=owner_ref,
        connection_id="connection-1",
        connection_identity_version=3,
        configuration_capability_version="capability-v1",
        configuration_capability_source="真实探针",
        capabilities=("tools",),
        name="participant",
    )


def test_owner_ref_recovers_new_binding_when_sqlite_id_is_stale(tmp_path) -> None:
    """旧 binding A 已删、新 binding B 未回写 SQLite 时，关闭必须找到 B。"""

    store = BindingStore(tmp_path / "agent_sessions.json")
    participant = _participant(agent_session_id="stale-binding-a")
    store.put(_binding("owner-binding-b", participant.owner_ref))
    adapter = AgentHostParticipantSessionAdapter(SimpleNamespace(store=store))

    assert adapter.resolve_owned_session_id(participant) == "owner-binding-b"


def test_conflicting_recorded_binding_owner_requires_reconcile(tmp_path) -> None:
    """SQLite ID 若仍存在但属于别的 owner，不能把 not-found/close 当作已收敛。"""

    store = BindingStore(tmp_path / "agent_sessions.json")
    participant = _participant(agent_session_id="wrong-owner-binding")
    store.put(_binding("wrong-owner-binding", "discussion:other:participant:0:v1"))
    adapter = AgentHostParticipantSessionAdapter(SimpleNamespace(store=store))

    with pytest.raises(DiscussionRuntimeError, match="其他 owner"):
        adapter.resolve_owned_session_id(participant)


def test_frozen_binding_accepts_effective_model_writeback() -> None:
    """runtime 写回有效模型不改变创建时冻结的 requested model 身份。"""

    participant = _participant(agent_session_id="binding")
    binding = replace(
        _binding("binding", participant.owner_ref),
        model="claude-sonnet-effective",
        effort="high",
    )

    AgentHostParticipantSessionAdapter._validate_frozen_binding(participant, binding)


@pytest.mark.anyio
async def test_participant_context_switches_reach_session_hub() -> None:
    """participant 的三个上下文开关必须按创建草稿传给 Session Hub。"""

    participant = _participant(agent_session_id=None)
    binding = _binding("binding", participant.owner_ref)

    class CapturingHub:
        """捕获 participant adapter 交给 Session Hub 的真实创建请求。"""

        def __init__(self) -> None:
            """创建没有既有 owner binding 的最小 Hub 替身。"""

            self.store = SimpleNamespace(find_by_owner_ref=lambda _owner: None)
            self.request = None

        async def create_complete_session(self, request, *, frozen_connection):
            """保存请求并返回与冻结配置一致的 binding。"""

            self.request = request
            assert frozen_connection.capability_version == "capability-v1"
            return binding

        async def close_result(self, *_args, **_kwargs):
            """测试失败清理路径保持可调用。"""

            return SimpleNamespace(status="closed")

    hub = CapturingHub()
    adapter = AgentHostParticipantSessionAdapter(hub)

    await adapter.ensure_session(participant, workdir="/tmp")

    assert hub.request is not None
    assert hub.request.memory_enabled is True
    assert hub.request.profile_enabled is False
    assert hub.request.self_enabled is True
    assert hub.request.permission_mode == "acceptEdits"
    assert hub.request.permission_preset is None
    assert hub.request.session_kind == "discussion"
    assert hub.request.memory_eligibility is False
    assert hub.request.agent_mcp_enabled is False


@pytest.mark.anyio
async def test_codex_participant_permission_reaches_session_hub() -> None:
    """Codex participant 必须使用创建时冻结的权限预设，不能回退成只读。"""

    participant = _participant(
        agent_session_id=None,
        runtime=Runtime.CODEX,
        permission_mode=None,
        permission_preset="workspace-write",
    )
    binding = replace(
        _binding("binding", participant.owner_ref),
        runtime=Runtime.CODEX,
        permission="Workspace write · on-request",
        permission_preset="workspace-write",
    )

    class CapturingHub:
        """捕获 Codex participant 会话创建请求。"""

        def __init__(self) -> None:
            """创建没有既有 owner binding 的最小 Hub 替身。"""

            self.store = SimpleNamespace(find_by_owner_ref=lambda _owner: None)
            self.request = None

        async def create_complete_session(self, request, *, frozen_connection):
            """保存请求并返回同一冻结权限的 binding。"""

            self.request = request
            return binding

        async def close_result(self, *_args, **_kwargs):
            """测试失败清理路径保持可调用。"""

            return SimpleNamespace(status="closed")

    hub = CapturingHub()
    adapter = AgentHostParticipantSessionAdapter(hub)

    await adapter.ensure_session(participant, workdir="/tmp")

    assert hub.request is not None
    assert hub.request.permission_mode is None
    assert hub.request.permission_preset == "workspace-write"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_model", "opus"),
        ("requested_effort", "high"),
        ("memory_enabled", False),
        ("profile_enabled", True),
        ("self_enabled", False),
    ],
)
def test_frozen_binding_rejects_changed_participant_configuration(
    field: str,
    value: str,
) -> None:
    """owner_ref 相同也不能认领请求模型或思考强度已经变化的 binding。"""

    participant = _participant(agent_session_id="binding")
    binding = replace(_binding("binding", participant.owner_ref), **{field: value})

    with pytest.raises(DiscussionRuntimeError, match="已经变化"):
        AgentHostParticipantSessionAdapter._validate_frozen_binding(
            participant,
            binding,
        )
