from __future__ import annotations

import asyncio


from trowel_py.codex_host import (
    CodexEventType,
    CodexHostManagerState,
    CodexSession,
    CodexSessionConfig,
)
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host._manager_support import (
    _behavior_server,
    _cfg,
    _deltas,
    _has,
    _init_resp,
    _manager,
    _restart_manager,
    _thread_result,
)


async def test_ensure_ready_reuses_one_client_across_sessions() -> None:

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session_a = CodexSession(_cfg("s1"))
    session_b = CodexSession(_cfg("s2"))
    manager.register(session_a)
    manager.register(session_b)

    await manager.send(session_a, "hi")
    client_after_a = manager.client
    assert client_after_a is not None
    await asyncio.sleep(0.02)

    await manager.send(session_b, "yo")
    assert manager.client is client_after_a
    assert manager.state is CodexHostManagerState.READY
    await manager.close()


async def test_effort_sent_on_turn_start_not_thread_start() -> None:

    thread_start_params: dict | None = None
    turn_start_params: dict | None = None

    async def behavior():
        nonlocal thread_start_params, turn_start_params
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        thread_start_params = msg["params"]
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        turn_start_params = msg["params"]
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "completed", "durationMs": 1},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(CodexSessionConfig("s1", "/tmp/x", effort="high"))
    manager.register(session)
    await manager.send(session, "hi")
    await asyncio.sleep(0.05)

    assert thread_start_params is not None and turn_start_params is not None
    assert "effort" not in thread_start_params
    assert turn_start_params.get("effort") == "high"
    await manager.close()


async def test_restart_resumes_same_thread_on_next_send() -> None:

    async def behavior1():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.02)
        yield Step.exit(0)

    async def behavior2():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        assert msg["method"] == "thread/resume"
        assert msg["params"]["threadId"] == "t-1"
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-2"}}})
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-2", "status": "completed", "durationMs": 3},
                },
            }
        )
        yield Step.recv()

    fake1 = FakeAppServer(behavior1())
    fake2 = FakeAppServer(behavior2())
    manager = _restart_manager([fake1, fake2])
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.send(session, "first")
    await asyncio.sleep(0.05)
    assert manager.state is CodexHostManagerState.DEGRADED
    assert session.binding is not None and session.binding.thread_id == "t-1"

    await manager.send(session, "second after restart")
    await asyncio.sleep(0.05)
    assert manager.state is CodexHostManagerState.READY
    events = session.drain()

    assert any(
        e.type is CodexEventType.HOST_STATUS and e.payload.get("status") == "ready"
        for e in events
    )

    assert _has(events, CodexEventType.FINISHED)

    assert any(m["method"] == "thread/resume" for m in fake2.received)
    await manager.close()
