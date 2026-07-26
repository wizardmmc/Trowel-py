from __future__ import annotations

import asyncio

import pytest

from trowel_py.codex_host import CodexEventType, CodexSession, CodexSessionState
from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.session import TurnConflictError
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import (
    _behavior_server,
    _cfg,
    _init_resp,
    _manager,
    _thread_result,
)


async def test_manager_lists_commands_for_connected_validated_version() -> None:
    fake = FakeAppServer(_behavior_server())
    manager = _manager(fake)

    roster = await manager.list_commands()

    assert [command["name"] for command in roster] == [
        "status",
        "compact",
        "review",
        "goal",
        "diff",
    ]
    await manager.close()


async def test_compact_reserves_idle_session_and_sends_native_request() -> None:
    fake = FakeAppServer(_behavior_server())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.compact(session)

    compact = next(msg for msg in fake.received if msg.get("method") == "thread/compact/start")
    assert compact["params"] == {"threadId": "t-1"}
    with pytest.raises(TurnConflictError):
        session.begin_send()
    session.abort_send()
    await manager.close()


async def test_compact_native_turn_owns_reservation_until_terminal() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "compact-turn", "status": "inProgress", "items": []},
                },
            }
        )
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "compact-turn", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    manager = _manager(FakeAppServer(behavior()))
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.compact(session)

    with pytest.raises(TurnConflictError):
        session.begin_send()
    async with asyncio.timeout(1):
        async for event in session.events():
            if event.type is CodexEventType.TURN_STARTED:
                break
    assert event.turn_id == "compact-turn"
    assert event.payload["autonomous"] is True
    assert event.payload["memory_eligible"] is False
    assert session.state is CodexSessionState.RUNNING

    async with asyncio.timeout(1):
        async for event in session.events():
            if event.type is CodexEventType.FINISHED:
                break
    assert session.state is CodexSessionState.IDLE
    session.begin_send()
    session.abort_send()
    await manager.close()


async def test_compact_protocol_failure_releases_session_reservation() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send(
            {
                "id": msg["id"],
                "error": {"code": -32600, "message": "compact unavailable"},
            }
        )
        yield Step.recv()

    manager = _manager(FakeAppServer(behavior()))
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    with pytest.raises(ProtocolViolationError, match="compact unavailable"):
        await manager.compact(session)

    session.begin_send()
    session.abort_send()
    await manager.close()


async def test_review_uses_schema_target_and_starts_autonomous_turn_without_user() -> None:
    captured: list[dict] = []

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        captured.append(msg)
        yield Step.send(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "review-turn", "status": "inProgress", "items": []},
                },
            }
        )
        yield Step.send(
            {
                "id": msg["id"],
                "result": {
                    "reviewThreadId": "t-1",
                    "turn": {"id": "review-turn", "status": "inProgress", "items": []},
                },
            }
        )
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "review-turn", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    result = await manager.start_review(
        session,
        {"type": "commit", "sha": "abc123", "title": "Fix auth"},
    )

    assert captured[0]["method"] == "review/start"
    assert captured[0]["params"] == {
        "threadId": "t-1",
        "target": {"type": "commit", "sha": "abc123", "title": "Fix auth"},
        "delivery": "inline",
    }
    assert result == {"review_thread_id": "t-1", "turn_id": "review-turn"}
    started = session.drain()
    assert any(
        event.type is CodexEventType.TURN_STARTED
        and event.turn_id == "review-turn"
        and event.payload["autonomous"] is True
        and event.payload["memory_eligible"] is False
        for event in started
    )
    assert not any(event.type is CodexEventType.USER for event in started)

    await asyncio.sleep(0.05)
    assert any(event.type is CodexEventType.FINISHED for event in session.drain())
    await manager.close()


async def test_review_protocol_failure_releases_session_reservation() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"reviewThreadId": "t-1"}})
        yield Step.recv()

    manager = _manager(FakeAppServer(behavior()))
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    with pytest.raises(ProtocolViolationError):
        await manager.start_review(session, {"type": "uncommittedChanges"})

    session.begin_send()
    session.abort_send()
    await manager.close()
