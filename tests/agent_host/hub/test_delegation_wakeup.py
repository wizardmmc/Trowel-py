"""验证委派通知通过 Session Hub 唤醒两种父 runtime。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.delegation_wakeup import DelegationWakeupCoordinator
from trowel_py.agent_host.hub import SessionHub
from trowel_py.codex_host.events import CodexEvent, CodexEventType, immutable_payload
from tests.agent_host.hub._support import (
    FakeCodexManager,
    FakeCodexSession,
    cc_req,
)


def _snapshot(parent_session_id: str, *, status: str) -> dict[str, object]:
    """构造指向指定父会话的委派终态通知。"""

    return {
        "delegation_id": "delegation-1",
        "version": 2,
        "parent": {"trowel_session_id": parent_session_id},
        "child": {"runtime": "claude_code", "trowel_session_id": "child-1"},
        "status": status,
        "needs_guidance": None,
        "reported": {"answer": "child result"},
        "observed": {"terminal_event": "finished"},
        "error": "child failed" if status == "failed" else None,
    }


async def test_claude_parent_receives_notice_through_session_hub(
    hub: SessionHub, workdir: Path
) -> None:
    """Claude Code 父会话通过统一 Hub 启动内部续轮。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]
    received = asyncio.Event()
    prompts: list[str] = []

    async def send(text: str):
        host.running = True
        prompts.append(text)
        try:
            yield {"type": "finished", "duration_ms": 1}
        finally:
            host.running = False
            received.set()

    host.send = send
    coordinator = DelegationWakeupCoordinator(hub)

    await coordinator.publish(_snapshot(binding.session_id, status="completed"))
    await asyncio.wait_for(received.wait(), timeout=0.2)

    assert len(prompts) == 1
    assert "child result" in prompts[0]
    await coordinator.shutdown()


async def test_codex_parent_receives_notice_through_session_hub(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    """Codex 父会话复用同一通知协调器和统一 turn 入口。"""

    session_id = "codex-parent"
    turn_id = "automatic-turn"
    binding = make_binding(
        session_id=session_id,
        runtime=Runtime.CODEX,
        native_session_id="thread-parent",
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission="danger-full-access",
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools",),
        name="parent",
    )
    hub.store.put(binding)
    drained = asyncio.Event()

    class ParentSession(FakeCodexSession):
        async def events(self):
            try:
                async for event in super().events():
                    yield event
            finally:
                drained.set()

    session = ParentSession(
        session_id,
        [
            CodexEvent(
                session_id=session_id,
                seq=1,
                type=CodexEventType.FINISHED,
                thread_id="thread-parent",
                turn_id=turn_id,
                payload=immutable_payload(status="completed"),
            )
        ],
        thread_id="thread-parent",
    )
    codex_mgr.register(session)
    coordinator = DelegationWakeupCoordinator(hub)

    await coordinator.publish(_snapshot(session_id, status="failed"))
    await asyncio.wait_for(drained.wait(), timeout=0.2)

    assert len(codex_mgr.sent) == 1
    sent_session_id, prompt = codex_mgr.sent[0]
    assert sent_session_id == session_id
    assert "child failed" in prompt
    await coordinator.shutdown()
