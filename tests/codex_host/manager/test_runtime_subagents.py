from __future__ import annotations

import asyncio
import json
from pathlib import Path

from trowel_py.codex_host import CodexEventType, CodexSession
from tests.codex_host._fake import FakeAppServer
from tests.codex_host.manager.support import (
    _behavior_server,
    _cfg,
    _manager,
    _server_request_fixture,
)


def _recorded_notifications() -> list[dict]:
    path = Path(__file__).parents[1] / "fixtures" / "subagent-0.144.0.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def test_child_notifications_route_to_parent_without_finishing_parent_turn() -> None:
    manager = _manager(FakeAppServer(_behavior_server()))
    session = CodexSession(_cfg("session-parent"))
    manager.register(session)
    await manager.attach(session)
    assert session.thread_id is not None
    session.record_native_turn_started("parent-turn-1")
    session.drain()

    for recorded in _recorded_notifications()[:5]:
        params = dict(recorded["params"])
        if params.get("threadId") == "parent-thread-1":
            params["threadId"] = session.thread_id
        manager._on_notification(recorded["method"], params)  # noqa: SLF001

    events = session.drain()
    child_events = [event for event in events if event.thread_id == "child-thread-1"]

    assert manager.session_for_thread("child-thread-1") is session
    assert [event.type for event in child_events] == [
        CodexEventType.STATUS,
        CodexEventType.TURN_STARTED,
        CodexEventType.ASSISTANT_DELTA,
        CodexEventType.FINISHED,
    ]
    assert session.current_turn_id == "parent-turn-1"
    assert session.state.value == "running"
    await manager.close()


async def test_unregister_removes_child_thread_ownership() -> None:
    manager = _manager(FakeAppServer(_behavior_server()))
    session = CodexSession(_cfg("session-parent"))
    manager.register(session)
    await manager.attach(session)
    recorded = _recorded_notifications()[0]
    params = {**recorded["params"], "threadId": session.thread_id}
    manager._on_notification(recorded["method"], params)  # noqa: SLF001

    manager.unregister(session.session_id)

    assert manager.session_for_thread("child-thread-1") is None
    await manager.close()


async def test_child_approval_keeps_child_source_and_parent_turn_running() -> None:
    manager = _manager(FakeAppServer(_behavior_server()))
    session = CodexSession(_cfg("session-parent"))
    manager.register(session)
    await manager.attach(session)
    session.record_native_turn_started("parent-turn-1")
    session.drain()
    recorded = _recorded_notifications()[0]
    manager._on_notification(  # noqa: SLF001
        recorded["method"],
        {**recorded["params"], "threadId": session.thread_id},
    )
    session.drain()
    approval = _server_request_fixture("server-request-approval.jsonl")
    params = {
        **approval["params"],
        "threadId": "child-thread-1",
        "turnId": "child-turn-1",
        "itemId": "child-command-1",
    }

    waiting = asyncio.create_task(
        manager._handle_server_request(  # noqa: SLF001
            manager.connection_generation,
            17,
            approval["method"],
            params,
        )
    )
    await asyncio.sleep(0)
    pending = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )

    assert pending.thread_id == "child-thread-1"
    assert pending.payload["thread_id"] == "child-thread-1"
    assert session.current_turn_id == "parent-turn-1"
    assert session.state.value == "running"

    manager.answer_request(
        session.session_id,
        str(pending.payload["request_id"]),
        "accept",
    )

    assert await waiting == {"decision": "accept"}
    answered = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )
    assert answered.thread_id == "child-thread-1"
    assert answered.payload["status"] == "answered"
    assert answered.payload["decision"] == "accept"
    assert session.current_turn_id == "parent-turn-1"
    await manager.close()
