from __future__ import annotations

import asyncio

import pytest

from trowel_py.codex_host import (
    CodexEventType,
    CodexSession,
    CodexSessionConfig,
)
from trowel_py.codex_host.session import TurnConflictError
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import (
    _behavior_server,
    _cfg,
    _deltas,
    _has,
    _init_resp,
    _manager,
    _thread_result,
)


async def test_two_concurrent_threads_are_isolated() -> None:

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session_a = CodexSession(_cfg("sA"))
    session_b = CodexSession(_cfg("sB"))
    manager.register(session_a)
    manager.register(session_b)

    await asyncio.gather(
        manager.send(session_a, "helloA"),
        manager.send(session_b, "helloB"),
    )
    await asyncio.sleep(0.05)

    events_a = session_a.drain()
    events_b = session_b.drain()

    for event in events_a:
        if event.thread_id is not None:
            assert event.thread_id == session_a.thread_id
    for event in events_b:
        if event.thread_id is not None:
            assert event.thread_id == session_b.thread_id

    for events in (events_a, events_b):
        types = {e.type for e in events}
        assert CodexEventType.SESSION_STARTED in types
        assert CodexEventType.USER in types
        assert CodexEventType.TURN_STARTED in types
        assert CodexEventType.ASSISTANT_DELTA in types
        assert CodexEventType.ASSISTANT_MESSAGE in types
        assert CodexEventType.FINISHED in types

    text_a = "".join(
        e.payload.get("delta", "")
        for e in events_a
        if e.type is CodexEventType.ASSISTANT_DELTA
    )
    text_b = "".join(
        e.payload.get("delta", "")
        for e in events_b
        if e.type is CodexEventType.ASSISTANT_DELTA
    )
    assert text_a == f"hi-{session_a.thread_id}"
    assert text_b == f"hi-{session_b.thread_id}"
    assert session_a.thread_id != session_b.thread_id

    await manager.close()


async def test_second_turn_reuses_thread_loaded_in_current_connection() -> None:

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session = CodexSession(CodexSessionConfig("s1", "/tmp/x", ephemeral=True))
    manager.register(session)

    await manager.send(session, "first")
    await asyncio.sleep(0.05)
    session.drain()

    await manager.send(session, "second")
    await asyncio.sleep(0.05)
    events = session.drain()

    methods = [message["method"] for message in fake.received if "method" in message]
    assert methods.count("thread/start") == 1
    assert "thread/resume" not in methods
    assert methods.count("turn/start") == 2
    assert _has(events, CodexEventType.FINISHED)
    await manager.close()


async def test_attach_resumes_thread_without_starting_turn() -> None:
    async def behavior():
        initialize = yield Step.recv()
        yield _init_resp(initialize["id"])
        yield Step.recv()
        resume = yield Step.recv()
        assert resume["method"] == "thread/resume"
        yield Step.send(
            {"id": resume["id"], "result": _thread_result("thread-existing")}
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(
        CodexSessionConfig(
            "resume-only",
            "/tmp/x",
            initial_thread_id="thread-existing",
        )
    )
    manager.register(session)
    # 模拟旧连接刚断、EOF watcher 尚未清理 attachment 标记的窗口。
    manager._attached_session_ids.add(session.session_id)  # noqa: SLF001

    binding = await manager.attach(session)

    assert binding.thread_id == "thread-existing"
    methods = [message["method"] for message in fake.received if "method" in message]
    assert methods.count("thread/resume") == 1
    assert "turn/start" not in methods
    await manager.close()


async def test_live_sessions_cannot_share_one_native_thread() -> None:

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    owner = CodexSession(_cfg("owner"))
    manager.register(owner)
    await manager.send(owner, "first")
    await asyncio.sleep(0.05)
    owner.drain()
    assert owner.thread_id is not None

    duplicate = CodexSession(
        CodexSessionConfig("duplicate", "/tmp/x", initial_thread_id=owner.thread_id)
    )
    manager.register(duplicate)

    with pytest.raises(TurnConflictError, match="already attached"):
        await manager.send(duplicate, "steal")
    assert manager.session_for_thread(owner.thread_id) is owner
    await manager.close()


async def test_concurrent_resume_atomically_claims_native_thread() -> None:

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        first = yield Step.recv()
        second = yield Step.recv()
        if second is None:
            # 正确实现会在关闭时到达 EOF；只有重复 resume 漏过时才收到第二个请求。
            return
        for request in (first, second):
            yield Step.send(
                {
                    "id": request["id"],
                    "error": {
                        "code": -32600,
                        "message": "duplicate resume reached app-server",
                    },
                }
            )

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    first = CodexSession(
        CodexSessionConfig("first", "/tmp/x", initial_thread_id="shared-thread")
    )
    second = CodexSession(
        CodexSessionConfig("second", "/tmp/x", initial_thread_id="shared-thread")
    )
    manager.register(first)
    manager.register(second)

    first_send = asyncio.create_task(manager.send(first, "first"))
    for _ in range(100):
        if any(message.get("method") == "thread/resume" for message in fake.received):
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail("first resume did not reach the fake app-server")

    try:
        with pytest.raises(TurnConflictError, match="already attached"):
            await manager.send(second, "second")
    finally:
        first_send.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_send
        await manager.close()


async def test_unregister_during_thread_attach_cannot_revive_session() -> None:

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.hold(0.05)
        yield Step.send({"id": msg["id"], "result": _thread_result("late-thread")})
        msg = yield Step.recv()
        if msg is None:
            return
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "ghost-turn"}}})
        msg = yield Step.recv()
        if msg is None:
            return
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("deleted"))
    manager.register(session)

    send_task = asyncio.create_task(manager.send(session, "hello"))
    for _ in range(100):
        if any(message.get("method") == "thread/start" for message in fake.received):
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail("thread/start did not reach the fake app-server")
    assert manager.unregister(session.session_id) is session

    with pytest.raises(TurnConflictError, match="no longer registered"):
        await send_task
    assert manager.get_session(session.session_id) is None
    assert manager.session_for_thread("late-thread") is None
    assert session.session_id not in manager._attached_session_ids  # noqa: SLF001
    assert not any(message.get("method") == "turn/start" for message in fake.received)
    assert not any(
        message.get("method") == "turn/interrupt" for message in fake.received
    )
    await manager.close()


async def test_binding_callback_runs_before_native_turn_start() -> None:

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    callback_methods: list[list[str]] = []

    def persist_binding(attached: CodexSession) -> None:

        assert attached.binding is not None
        callback_methods.append(
            [message["method"] for message in fake.received if "method" in message]
        )

    await manager.send(session, "hello", before_turn_start=persist_binding)
    await asyncio.sleep(0.05)

    assert callback_methods == [["initialize", "initialized", "thread/start"]]
    assert any(message.get("method") == "turn/start" for message in fake.received)
    await manager.close()


async def test_unregister_while_turn_start_waits_interrupts_native_turn() -> None:

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-delete")})
        msg = yield Step.recv()
        yield Step.hold(0.05)
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-delete"}}})
        msg = yield Step.recv()
        if msg is None:
            return
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("deleted-during-turn"))
    manager.register(session)

    send_task = asyncio.create_task(manager.send(session, "hello"))
    for _ in range(100):
        if any(message.get("method") == "turn/start" for message in fake.received):
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail("turn/start did not reach the fake app-server")
    assert manager.unregister(session.session_id) is session

    with pytest.raises(TurnConflictError, match="no longer registered"):
        await send_task
    assert any(message.get("method") == "turn/interrupt" for message in fake.received)
    assert session.state.name == "IDLE"
    assert manager.session_for_thread("t-delete") is None
    await manager.close()
