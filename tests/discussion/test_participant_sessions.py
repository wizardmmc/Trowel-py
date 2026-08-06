"""验证 participant binding 的冻结配置与 owner_ref 崩溃恢复。"""

from __future__ import annotations

from types import SimpleNamespace
from dataclasses import replace

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.store import BindingStore
from trowel_py.discussion.errors import DiscussionRuntimeError
from trowel_py.discussion.models import DiscussionParticipant
from trowel_py.discussion.participant_sessions import AgentHostParticipantSessionAdapter


def _participant(*, agent_session_id: str | None) -> DiscussionParticipant:
    """构造带 stale SQLite session ID 的最小冻结参与者。"""

    return DiscussionParticipant(
        id="participant-1",
        discussion_id="discussion-1",
        position=0,
        name="glm",
        runtime=Runtime.CLAUDE_CODE,
        connection_id="connection-1",
        model="sonnet",
        effort=None,
        session_configuration_id="configuration-1",
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
        permission="dontAsk",
        memory_enabled=False,
        profile_enabled=False,
        self_enabled=False,
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


@pytest.mark.parametrize(
    ("field", "value"),
    [("requested_model", "opus"), ("requested_effort", "high")],
)
def test_frozen_binding_rejects_changed_requested_model_or_effort(
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
