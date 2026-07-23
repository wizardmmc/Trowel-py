from __future__ import annotations

import asyncio

import pytest

from trowel_py.codex_host import (
    CodexEventType,
    CodexHostManagerState,
    CodexSession,
    TransportClosedError,
)
from trowel_py.codex_host.session import TurnConflictError
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host._manager_support import (
    _behavior_server,
    _cfg,
    _has,
    _init_resp,
    _manager,
    _thread_result,
)


async def test_concurrent_send_same_session_is_rejected() -> None:

    fake = FakeAppServer(_behavior_server(block_on_turn_start=True))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    first = asyncio.create_task(manager.send(session, "first"))
    await asyncio.sleep(0.05)
    with pytest.raises(TurnConflictError):
        await manager.send(session, "second")

    first.cancel()
    try:
        await first
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    session.abort_send()
    await manager.close()


async def test_eof_marks_running_turn_host_exited() -> None:

    fake = FakeAppServer(_behavior_server(exit_after_turn=True))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.send(session, "hi")
    await asyncio.sleep(0.05)

    await asyncio.sleep(0.05)
    assert manager.state is CodexHostManagerState.DEGRADED
    await manager.close()


async def test_eof_while_running_pushes_host_exited_terminal() -> None:

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.02)
        yield Step.exit(0)

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "hi")
    await asyncio.sleep(0.1)

    assert manager.state is CodexHostManagerState.DEGRADED
    events = session.drain()
    assert any(
        e.type is CodexEventType.HOST_STATUS
        and e.payload.get("status") == "host_exited"
        for e in events
    )

    assert session.binding is not None
    assert session.binding.thread_id == "t-1"
    await manager.close()


async def test_eof_during_turn_start_window_does_not_deadlock() -> None:

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        yield Step.recv()
        yield Step.hold(0.05)
        yield Step.exit(1)

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    with pytest.raises(TransportClosedError):
        await manager.send(session, "hi")
    await asyncio.sleep(0.1)
    assert manager.state is CodexHostManagerState.DEGRADED

    session.begin_send()
    await manager.close()


async def test_interrupt_sends_request_and_closes_turn_interrupted() -> None:

    fake = FakeAppServer(_behavior_server(block_on_turn_start=True))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    send_task = asyncio.create_task(manager.send(session, "hi"))
    await asyncio.sleep(0.05)

    send_task.cancel()
    try:
        await send_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    await manager.close()

    # 前一个 fake 故意卡在 turn/start，另起完整脚本才能验证 interrupt 握手。
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.02)
        msg = yield Step.recv()
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": msg["params"]["threadId"],
                    "turn": {"id": msg["params"]["turnId"], "status": "interrupted"},
                },
            }
        )
        yield Step.recv()

    fake2 = FakeAppServer(behavior())
    manager2 = _manager(fake2)
    session2 = CodexSession(_cfg("s2"))
    manager2.register(session2)
    await manager2.send(session2, "hi")
    await manager2.interrupt(session2)
    await asyncio.sleep(0.05)
    events = session2.drain()
    assert _has(events, CodexEventType.INTERRUPTED)
    assert any(m["method"] == "turn/interrupt" for m in fake2.received)
    await manager2.close()
