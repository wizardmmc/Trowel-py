from __future__ import annotations

import asyncio
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


async def test_stream_cc_writes_back_native_before_consumer_closes(
    hub: SessionHub, workdir: Path
) -> None:
    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        host.cc_session_id = "native-before-terminal"
        yield {
            "type": "session_started",
            "model": "glm-5.2",
            "cwd": str(workdir),
            "cc_session_id": "native-before-terminal",
            "tools": [],
        }
        yield {"type": "finished", "duration_ms": 1}

    host.send = send
    stream = hub.stream(binding.session_id, "hello")
    first = await anext(stream)
    assert first["type"] == "session_started"
    await stream.aclose()

    persisted = hub.get(binding.session_id)
    assert persisted is not None
    assert persisted.native_session_id == "native-before-terminal"


async def test_wait_until_idle_resumes_after_parent_stream_finishes(
    hub: SessionHub, workdir: Path
) -> None:
    """内部通知只等待实时 turn，不通过模型或 MCP 状态查询探活。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]
    started = asyncio.Event()
    release = asyncio.Event()

    async def send(_text: str):
        host.running = True
        started.set()
        try:
            await release.wait()
            yield {"type": "finished", "duration_ms": 1}
        finally:
            host.running = False

    host.send = send
    turn = asyncio.create_task(
        _collect(hub.stream(binding.session_id, "parent work"))
    )
    await started.wait()
    waiter = asyncio.create_task(hub.wait_until_idle(binding.session_id))
    await asyncio.sleep(0)

    assert not waiter.done()

    release.set()
    await turn
    await asyncio.wait_for(waiter, timeout=0.1)


async def _collect(stream):
    """把异步事件流消费到结束，供并发时序测试使用。"""

    return [event async for event in stream]
