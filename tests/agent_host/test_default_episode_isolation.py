from __future__ import annotations

import pytest

from trowel_py.agent_host.episode_adapter import AgentEpisodeRuntimeAdapter
from trowel_py.model_os.episode_starting import StartEpisodeCommand
from trowel_py.model_os.types import MemoryEligibility, SessionPurpose


class Hub:
    def __init__(self) -> None:
        self.request = None

    def create(self, request):
        self.request = request
        return type("Binding", (), {"session_id": "session"})()

    async def start_native(self, _session_id):
        return type(
            "Identity",
            (),
            {
                "agent_session_id": "session",
                "runtime": self.request.runtime,
                "native_session_id": "native",
                "runtime_generation": "generation",
                "runtime_pid": None,
                "runtime_pgid": None,
            },
        )()


def command(runtime: str) -> StartEpisodeCommand:
    return StartEpisodeCommand(
        work_item_id="work",
        task_id=None,
        previous_episode_id=None,
        previous_snapshot_ref=None,
        runtime=runtime,
        model="deep",
        effort="high",
        memory_enabled=False,
        profile_enabled=False,
        workdir="/isolated",
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
        permission="read-only" if runtime == "codex" else "bypassPermissions",
        idempotency_key="idempotency",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["claude_code", "codex"])
async def test_default_episode_uses_ineligible_no_mcp_isolation(runtime) -> None:
    hub = Hub()
    await AgentEpisodeRuntimeAdapter(hub).start_native(command(runtime), object())

    request = hub.request
    assert request.session_kind == "default"
    assert request.session_purpose == "default"
    assert request.memory_eligibility is False
    assert request.memory_eligibility_mode == "ineligible"
    assert request.memory_enabled is False
    assert request.profile_enabled is False
    assert request.agent_mcp_enabled is False
    assert request.model_os_mcp_enabled is False
    if runtime == "claude_code":
        assert request.native_tools_mode == "none"
    else:
        assert request.approval_policy == "never"
        assert request.sandbox == "read-only"
