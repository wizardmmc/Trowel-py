from pathlib import Path

from trowel_py.agent_host.schemas import CreateAgentSessionRequest


def test_default_cc_binding_records_effective_launch_permission(
    hub, workdir: Path
) -> None:
    binding = hub.create(
        CreateAgentSessionRequest(runtime="claude_code", workdir=str(workdir))
    )

    assert binding.permission == "bypassPermissions"


def test_delegate_cc_metadata_reaches_real_host_factory(
    hub, workdir: Path, cc_registry
) -> None:
    binding = hub.create(
        CreateAgentSessionRequest(
            runtime="claude_code",
            workdir=str(workdir),
            permission_mode="bypassPermissions",
            session_kind="delegate",
            memory_eligibility=False,
            agent_mcp_enabled=False,
            parent_session_id="parent-codex",
            delegation_depth=1,
        )
    )

    host = cc_registry[binding.session_id]
    assert host.session_kind == "delegate"
    assert host.agent_mcp_enabled is False
    assert binding.session_kind == "delegate"
    assert binding.memory_eligibility is False
    assert binding.parent_session_id == "parent-codex"
    assert binding.delegation_depth == 1


def test_delegate_codex_has_memory_but_no_recursive_agent(
    hub, workdir: Path, codex_mgr
) -> None:
    binding = hub.create(
        CreateAgentSessionRequest(
            runtime="codex",
            workdir=str(workdir),
            permission_preset="danger-full-access",
            memory_enabled=True,
            session_kind="delegate",
            memory_eligibility=False,
            agent_mcp_enabled=False,
            parent_session_id="parent-cc",
            delegation_depth=1,
        )
    )

    session = codex_mgr.get_session(binding.session_id)
    assert session.config.trowel_memory_mcp is not None
    assert session.config.trowel_agent_mcp is None
    assert binding.declared_mcp_roster == ("trowel_note_search",)
    assert binding.session_kind == "delegate"
    assert binding.memory_eligibility is False
