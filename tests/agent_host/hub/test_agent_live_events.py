"""验证两种 runtime 共用常驻 Agent 事件订阅。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from trowel_py.agent_host.hub import SessionHub
from tests.agent_host.hub._support import cc_req


async def test_claude_subscriber_can_wait_before_lazy_process_start(
    hub: SessionHub, workdir: Path
) -> None:
    """新建 Claude 会话尚未拉起进程时也能先建立常驻订阅。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        host.running = True
        try:
            yield {"type": "text", "text": "first turn"}
            yield {"type": "finished", "duration_ms": 1}
        finally:
            host.running = False

    host.send = send
    live = hub.subscribe_agent_events(binding.session_id)
    first_event = asyncio.create_task(anext(live))
    await asyncio.sleep(0)
    assert not first_event.done()

    turn = asyncio.create_task(_collect(hub.stream(binding.session_id, "hello")))
    first = await asyncio.wait_for(first_event, timeout=0.2)
    await turn
    await live.aclose()

    assert first["type"] == "text"
    assert first["payload"]["text"] == "first turn"


async def test_claude_automatic_turn_reaches_live_subscriber(
    hub: SessionHub, workdir: Path
) -> None:
    """Agent Host 启动的 Claude 续轮在没有消息请求方时仍进入实时界面。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        host.running = True
        try:
            yield {
                "type": "turn_start",
                "turn_id": "cc-auto-turn",
                "revertible": False,
            }
            yield {"type": "text", "text": "automatic result"}
            yield {"type": "finished", "duration_ms": 1}
        finally:
            host.running = False

    host.send = send
    live = hub.subscribe_agent_events(binding.session_id)
    turn = asyncio.create_task(
        _collect(hub.run_automatic_turn(binding.session_id, "internal notice"))
    )

    first = await asyncio.wait_for(anext(live), timeout=0.2)
    second = await asyncio.wait_for(anext(live), timeout=0.2)
    third = await asyncio.wait_for(anext(live), timeout=0.2)
    await turn
    await live.aclose()

    assert first["type"] == "turn_start"
    assert first["payload"]["autonomous"] is True
    assert second["payload"]["text"] == "automatic result"
    assert third["type"] == "finished"


async def _collect(stream):
    """把内部续轮事件消费到终态。"""

    return [event async for event in stream]
