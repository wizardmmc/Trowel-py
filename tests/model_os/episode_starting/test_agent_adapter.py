from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.episode_adapter import (
    AgentEpisodeRuntimeAdapter,
    CcOrphanReaper,
)
from trowel_py.model_os.episode_starting import (
    NativeSessionIdentity,
    StartEpisodeCommand,
)
from trowel_py.model_os.types import MemoryEligibility, SessionPurpose


class FakeHub:
    def __init__(self) -> None:
        self.requests = []
        self.started = []
        self.streamed = []
        self.identity = NativeSessionIdentity(
            agent_session_id="agent-1",
            runtime="codex",
            native_session_id="thread-1",
            runtime_generation="codex-connection-1",
            runtime_pid=100,
            runtime_pgid=None,
        )

    def create(self, request):
        self.requests.append(request)

        class Binding:
            session_id = "agent-1"

        return Binding()

    async def start_native(self, session_id):
        self.started.append(session_id)
        return self.identity

    def persist_native_identity(self, identity):
        assert identity == self.identity

    def native_identity(self, session_id):
        assert session_id == "agent-1"
        return self.identity

    async def stream(self, session_id, text):
        self.streamed.append((session_id, text))
        yield {
            "session_id": session_id,
            "runtime": "codex",
            "type": "finished",
            "payload": {},
        }


def _command(workdir: Path, memory: bool, profile: bool) -> StartEpisodeCommand:
    return StartEpisodeCommand(
        work_item_id="work-1",
        task_id=None,
        previous_episode_id=None,
        previous_snapshot_ref=None,
        runtime="codex",
        model="model-1",
        effort="high",
        memory_enabled=memory,
        profile_enabled=profile,
        workdir=str(workdir),
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
        permission="danger-full-access",
        idempotency_key=f"start-{memory}-{profile}",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("memory", "profile"),
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_adapter_preserves_four_mp_combinations(
    tmp_path: Path, memory: bool, profile: bool
) -> None:
    hub = FakeHub()
    adapter = AgentEpisodeRuntimeAdapter(hub)

    identity = await adapter.start_native(
        _command(tmp_path, memory, profile), object()
    )

    request = hub.requests[0]
    assert request.resume_from is None
    assert request.memory_enabled is memory
    assert request.profile_enabled is profile
    assert request.model_os_mcp_enabled is True
    assert request.memory_eligibility is False
    assert identity.native_session_id == "thread-1"


@pytest.mark.anyio
async def test_adapter_keeps_start_persist_and_first_turn_separate(tmp_path: Path) -> None:
    hub = FakeHub()
    adapter = AgentEpisodeRuntimeAdapter(hub)
    identity = await adapter.start_native(_command(tmp_path, True, True), object())

    assert hub.started == ["agent-1"]
    assert hub.streamed == []
    await adapter.persist_binding(identity)
    events = [event async for event in adapter.start_first_turn(identity, "context")]

    assert hub.streamed == [("agent-1", "context")]
    assert events[-1]["type"] == "finished"


def test_cc_orphan_reaper_kills_only_matching_private_process_group() -> None:
    killed = []
    reaper = CcOrphanReaper(
        getpgid=lambda pid: pid,
        getpgrp=lambda: 999,
        killpg=lambda pgid, sig: killed.append((pgid, sig)),
        pid_alive=lambda _pid: True,
    )
    identity = NativeSessionIdentity(
        agent_session_id="agent-cc",
        runtime="claude_code",
        native_session_id=None,
        runtime_generation="cc-process-1",
        runtime_pid=321,
        runtime_pgid=321,
    )

    assert reaper.reap(identity) == "killed_private_process_group"
    assert killed and killed[0][0] == 321


@pytest.mark.parametrize(
    ("observed_pgid", "own_pgrp"), [(777, 999), (321, 321)]
)
def test_cc_orphan_reaper_refuses_mismatched_or_own_group(
    observed_pgid: int, own_pgrp: int
) -> None:
    reaper = CcOrphanReaper(
        getpgid=lambda _pid: observed_pgid,
        getpgrp=lambda: own_pgrp,
        killpg=lambda _pgid, _sig: pytest.fail("must not signal"),
        pid_alive=lambda _pid: True,
    )
    identity = NativeSessionIdentity(
        agent_session_id="agent-cc",
        runtime="claude_code",
        native_session_id=None,
        runtime_generation="cc-process-1",
        runtime_pid=321,
        runtime_pgid=321,
    )

    with pytest.raises(RuntimeError):
        reaper.reap(identity)
