from __future__ import annotations

from pathlib import Path

import pytest

from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    cc_req,
    codex_req,
)
from trowel_py.agent_host.hub import SessionHub


@pytest.mark.anyio
async def test_hub_routes_steer_to_cc_with_expected_identity(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    binding = hub.create(cc_req(workdir))
    host = cc_registry[binding.session_id]
    host.current_turn_id = "cc-turn-1"
    host.process_generation = "cc-process-1"

    await hub.steer(
        binding.session_id,
        "kernel-soft-yield",
        expected_turn_id="cc-turn-1",
        expected_generation="cc-process-1",
    )

    assert host.steered == [
        ("kernel-soft-yield", "cc-turn-1", "cc-process-1")
    ]
    assert hub.runtime_generation(binding.session_id) == "cc-process-1"


@pytest.mark.anyio
async def test_hub_routes_steer_to_codex_with_expected_identity(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    binding = hub.create(codex_req(workdir))
    codex_mgr.connection_generation = 7

    await hub.steer(
        binding.session_id,
        "kernel-soft-yield",
        expected_turn_id="codex-turn-1",
        expected_generation="7",
    )

    assert codex_mgr.steered == [
        (
            binding.session_id,
            "kernel-soft-yield",
            "codex-turn-1",
            "7",
        )
    ]
    assert hub.runtime_generation(binding.session_id) == "7"


@pytest.mark.anyio
async def test_model_os_observer_is_awaited_without_reusing_sync_observer(
    hub: SessionHub,
) -> None:
    observed: list[str] = []

    async def observer(payload) -> None:
        observed.append(str(payload["type"]))

    hub.set_model_os_observer(observer)
    await hub._observe_model_os({"type": "context_usage", "payload": {}})

    assert observed == ["context_usage"]


@pytest.mark.anyio
async def test_model_os_observer_exception_does_not_break_hub(
    hub: SessionHub,
) -> None:
    async def raising(_payload) -> None:
        raise RuntimeError("observer failed")

    hub.set_model_os_observer(raising)
    await hub._observe_model_os({"type": "context_usage", "payload": {}})
