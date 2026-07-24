from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.hub import (
    SessionHub,
    SessionNotFoundError,
)
from tests.agent_host.hub._support import (
    _is_envelope,
    cc_req,
)


async def test_stream_unknown_session_404(hub: SessionHub):
    with pytest.raises(SessionNotFoundError, match="session nope not found"):
        _ = [e async for e in hub.stream("nope", "hi")]


async def test_stream_cc_yields_unified_envelope(hub: SessionHub, workdir: Path):

    binding = hub.create(cc_req(workdir))
    events = [e async for e in hub.stream(binding.session_id, "hello")]
    assert events, "expected at least one event from the CC stream"
    assert all(_is_envelope(e) for e in events), events
    assert all(e["runtime"] == "claude_code" for e in events)
    assert all(e["session_id"] == binding.session_id for e in events)

    assert events[0]["type"] == "text"
    assert events[0]["payload"]["text"] == "echo:hello"

    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


async def test_stream_cc_seq_persists_across_turns(hub: SessionHub, workdir: Path):

    binding = hub.create(cc_req(workdir))
    first = [e async for e in hub.stream(binding.session_id, "one")]

    # adapter 跨 turn 复用，seq 不能在每次 send 时重置。
    second = [e async for e in hub.stream(binding.session_id, "two")]
    assert first[-1]["seq"] >= 1
    assert second[0]["seq"] == first[-1]["seq"] + 1, (
        "seq must continue from the prior turn, not reset to 1"
    )


async def test_stream_cc_writes_back_effective_effort_and_permission(
    hub: SessionHub, workdir: Path
) -> None:
    binding = hub.create(
        cc_req(
            workdir,
            model="opus",
            effort="max",
            permission_mode="acceptEdits",
        )
    )
    host = hub._cc_registry[binding.session_id]
    host.cc_session_id = "native-cc-config"

    _ = [event async for event in hub.stream(binding.session_id, "hello")]

    persisted = hub.get(binding.session_id)
    assert persisted is not None
    assert persisted.model == "opus"
    assert persisted.effort == "max"
    assert persisted.permission == "acceptEdits"
