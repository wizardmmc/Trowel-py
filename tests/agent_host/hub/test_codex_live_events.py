from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from tests.agent_host.hub._support import FakeCodexManager, FakeCodexSession
from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import SessionHub
from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    immutable_payload,
)


class ControlledSession:
    def __init__(self) -> None:
        self.session_id = "codex-live"
        self.state = "idle"
        self.events_calls = 0
        self.release = asyncio.Event()

    async def events(self) -> AsyncIterator[CodexEvent]:
        self.events_calls += 1
        await self.release.wait()
        yield CodexEvent(
            session_id=self.session_id,
            seq=1,
            type=CodexEventType.PLAN_UPDATED,
            thread_id="thread-1",
            turn_id="turn-1",
            payload=immutable_payload(
                explanation=None,
                steps=({"step": "Inspect", "status": "inProgress"},),
            ),
        )


async def test_codex_live_subscribers_share_one_native_reader(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    session = ControlledSession()
    codex_mgr.sessions[session.session_id] = session
    hub.store.put(
        make_binding(
            session_id=session.session_id,
            runtime=Runtime.CODEX,
            native_session_id="thread-1",
            workdir=str(workdir),
            model="gpt-5.6-sol",
            effort="high",
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools", "approval"),
            name="project",
        )
    )

    first = hub.subscribe_codex_events(session.session_id)
    second = hub.subscribe_codex_events(session.session_id)
    session.release.set()

    event_a, event_b = await asyncio.gather(anext(first), anext(second))
    await first.aclose()
    await second.aclose()

    assert session.events_calls == 1
    assert event_a == event_b
    assert event_a["type"] == "plan_updated"
    assert event_a["seq"] == 1


async def test_delete_stops_codex_native_reader(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    session = ControlledSession()
    codex_mgr.sessions[session.session_id] = session
    hub.store.put(
        make_binding(
            session_id=session.session_id,
            runtime=Runtime.CODEX,
            native_session_id="thread-1",
            workdir=str(workdir),
            model="gpt-5.6-sol",
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="project",
        )
    )

    stream = hub.subscribe_codex_events(session.session_id)
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await hub.delete(session.session_id)
    await asyncio.sleep(0)

    assert pending.done()
    await stream.aclose()


async def test_start_turn_observes_codex_events_without_a_client_subscriber(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    """后台观察器不能依赖 renderer 已经连上事件流。"""

    session = FakeCodexSession(
        "codex-live",
        [
            CodexEvent(
                session_id="codex-live",
                seq=1,
                type=CodexEventType.PLAN_UPDATED,
                thread_id="thread-1",
                turn_id="turn-1",
                payload=immutable_payload(
                    explanation=None,
                    steps=({"step": "Inspect", "status": "inProgress"},),
                ),
            )
        ],
        thread_id="thread-1",
    )
    codex_mgr.sessions[session.session_id] = session
    hub.store.put(
        make_binding(
            session_id=session.session_id,
            runtime=Runtime.CODEX,
            native_session_id="thread-1",
            workdir=str(workdir),
            model="gpt-5.6-sol",
            effort="high",
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools", "approval"),
            name="project",
        )
    )
    observed: list[dict[str, object]] = []
    hub._event_observer = observed.append  # noqa: SLF001

    await hub.start_codex_turn(session.session_id, "hello")
    await asyncio.sleep(0)

    assert [event["type"] for event in observed] == ["plan_updated"]
