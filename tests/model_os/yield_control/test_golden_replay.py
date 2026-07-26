from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.model_os._episode_helpers import activate_episode, make_running_system_episode
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.yielding import (
    ForceYieldReason,
    TurnRegistration,
    YieldCoordinator,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "runtime-control-golden.json"


class Runtime:
    async def interrupt(self, _session_id: str) -> None:
        return None

    async def release(self, _work_lease_id: str) -> None:
        return None


def _traces() -> list[dict[str, object]]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema"] == "model-os-runtime-control-golden-v1"
    return payload["traces"]


@pytest.mark.anyio
@pytest.mark.parametrize("trace", _traces(), ids=lambda item: item["name"])
async def test_l05_golden_trace_replays_to_frozen_outcome(
    store: ModelOsStore, trace: dict[str, object]
) -> None:
    runtime = Runtime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    name = str(trace["name"])
    if name == "interrupt_without_active_turn":
        receipt = await coordinator.request_forced(
            "session-1",
            ForceYieldReason.RUNTIME_TIMEOUT,
            expected_turn_id="turn-1",
            expected_generation="generation-1",
        )
        assert receipt.status == "no_action:no_active_turn"
        return

    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime_name = "codex" if trace["runtime"] == "codex" else "claude_code"
    await coordinator.register_turn(
        TurnRegistration(
            session_id="session-1",
            episode_id=episode.episode_id,
            runtime=runtime_name,
            turn_id="turn-1",
            generation="generation-1",
            native_session_id="native-1",
            ownership_lease_id=lease.lease_id,
            ownership_owner=lease.owner,
            ownership_token=lease.fencing_token,
            work_lease_id="work-lease-1",
        )
    )
    if name == "late_interrupt_after_terminal":
        await coordinator.observe(
            "session-1", {"type": "finished", "payload": {}}, generation="generation-1"
        )
        receipt = await coordinator.request_forced(
            "session-1",
            ForceYieldReason.USER_PREEMPT,
            expected_turn_id="turn-1",
            expected_generation="generation-1",
        )
        assert receipt.status == "no_action:stale_turn"
        return
    if name == "host_exit_active_turn":
        receipt = await coordinator.observe(
            "session-1",
            {"type": "session_exited", "payload": {"returncode": -9}},
            generation="generation-1",
        )
    else:
        if name == "cc_tool_interrupt_unknown_effect":
            await coordinator.observe(
                "session-1",
                {
                    "type": "tool_call",
                    "item_id": "tool-1",
                    "payload": {"tool_use_id": "tool-1", "tool_name": "Bash"},
                },
                generation="generation-1",
            )
        await coordinator.request_forced(
            "session-1",
            ForceYieldReason.RUNTIME_TIMEOUT,
            expected_turn_id="turn-1",
            expected_generation="generation-1",
        )
        terminal = (
            {"type": "interrupted", "payload": {"status": "interrupted"}}
            if name == "codex_native_interrupt"
            else {"type": "error", "payload": {"subclass": "error_during_execution"}}
        )
        receipt = await coordinator.observe(
            "session-1", terminal, generation="generation-1"
        )

    assert receipt is not None
    assert (receipt.status == "reconcile_required") is bool(
        trace["requires_reconcile"]
    )
