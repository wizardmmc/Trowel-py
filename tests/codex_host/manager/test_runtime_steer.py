from __future__ import annotations

import pytest

from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import (
    _cfg,
    _init_resp,
    _manager,
    _thread_result,
)
from trowel_py.codex_host import CodexSession
from trowel_py.codex_host.session import TurnConflictError


async def _active_manager():
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("thread-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        msg = yield Step.recv()
        assert msg["method"] == "turn/steer"
        assert msg["params"] == {
            "threadId": "thread-1",
            "expectedTurnId": "turn-1",
            "input": [{"type": "text", "text": "kernel-soft-yield"}],
        }
        yield Step.send({"id": msg["id"], "result": {"turnId": "turn-1"}})
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-1"))
    manager.register(session)
    await manager.send(session, "first")
    return manager, session, fake


@pytest.mark.anyio
async def test_steer_appends_input_to_matching_active_turn() -> None:
    manager, session, fake = await _active_manager()

    await manager.steer(
        session,
        "kernel-soft-yield",
        expected_turn_id="turn-1",
        expected_generation=str(manager.connection_generation),
    )

    assert any(message.get("method") == "turn/steer" for message in fake.received)
    await manager.close()


@pytest.mark.anyio
async def test_steer_rejects_stale_turn_and_generation_before_transport() -> None:
    manager, session, fake = await _active_manager()
    before = len(fake.received)

    with pytest.raises(TurnConflictError, match="stale turn"):
        await manager.steer(
            session,
            "ignored",
            expected_turn_id="turn-stale",
            expected_generation=str(manager.connection_generation),
        )
    with pytest.raises(TurnConflictError, match="stale connection generation"):
        await manager.steer(
            session,
            "ignored",
            expected_turn_id="turn-1",
            expected_generation="generation-stale",
        )

    assert len(fake.received) == before
    await manager.steer(
        session,
        "kernel-soft-yield",
        expected_turn_id="turn-1",
        expected_generation=str(manager.connection_generation),
    )
    await manager.close()


@pytest.mark.anyio
async def test_steer_rejects_idle_session_without_starting_app_server() -> None:
    fake = FakeAppServer(iter(()))
    manager = _manager(fake)
    session = CodexSession(_cfg("session-1"))
    manager.register(session)

    with pytest.raises(TurnConflictError, match="no active turn"):
        await manager.steer(
            session,
            "ignored",
            expected_turn_id="turn-1",
            expected_generation="0",
        )

    assert fake.received == []
    await manager.close()
