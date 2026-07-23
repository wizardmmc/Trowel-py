from __future__ import annotations

import asyncio


from trowel_py.codex_host import (
    CodexEventType,
    CodexSession,
)
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host._manager_support import (
    _cfg,
    _init_resp,
    _manager,
    _server_request_fixture,
    _thread_result,
)


async def test_command_approval_waits_for_answer_and_reuses_native_id() -> None:

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        answer = yield Step.recv()
        assert answer == {"id": 0, "result": {"decision": "accept"}}
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thr-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "run the probe")
    await asyncio.sleep(0.05)
    request_event = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )
    request_id = str(request_event.payload["request_id"])
    assert (
        request_event.payload["available_decisions"]
        == recorded["params"]["availableDecisions"]
    )

    answered = manager.answer_request("session-a", request_id, "accept")
    assert answered.status.value == "answered"
    await asyncio.sleep(0.05)
    assert any(
        event.type is CodexEventType.APPROVAL_REQUEST
        and event.payload["status"] == "answered"
        for event in session.drain()
    )
    await manager.close()


async def test_file_approval_without_context_is_auto_declined() -> None:

    recorded = _server_request_fixture("server-request-file-approval-075.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        answer = yield Step.recv()
        assert answer == {"id": 0, "result": {"decision": "decline"}}
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thr-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "edit the file")
    await asyncio.sleep(0.05)
    approval = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )
    assert approval.payload["approval_kind"] == "file_approval"
    assert approval.payload["status"] == "answered"
    assert approval.payload["decision"] == "decline"
    assert approval.payload["auto_resolved"] is True
    await manager.close()


async def test_unanswered_request_expires_with_safe_decline() -> None:

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        answer = yield Step.recv()
        assert answer == {"id": 0, "result": {"decision": "decline"}}
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake, pending_request_timeout_s=0.01)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "wait")
    await asyncio.sleep(0.05)
    approvals = [
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    ]
    assert [event.payload["status"] for event in approvals] == [
        "pending",
        "expired",
    ]
    await manager.close()


async def test_host_exit_marks_pending_request_host_closed() -> None:

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        yield Step.exit(1)

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "wait")
    await asyncio.sleep(0.08)
    events = session.drain()
    approvals = [
        event for event in events if event.type is CodexEventType.APPROVAL_REQUEST
    ]
    assert [event.payload["status"] for event in approvals] == [
        "pending",
        "host_closed",
    ]
    assert any(
        event.type is CodexEventType.HOST_STATUS
        and event.payload["status"] == "host_exited"
        for event in events
    )
    await manager.close()


async def test_interrupt_precedes_cancel_response_for_pending_approval() -> None:

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        interrupt = yield Step.recv()
        assert interrupt["method"] == "turn/interrupt"
        yield Step.send({"id": interrupt["id"], "result": {}})
        approval_answer = yield Step.recv()
        assert approval_answer == {"id": 0, "result": {"decision": "cancel"}}
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "wait")
    await asyncio.sleep(0.05)

    await manager.interrupt(session)
    await asyncio.sleep(0.05)

    approvals = [
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    ]
    assert [event.payload["status"] for event in approvals] == [
        "pending",
        "answered",
    ]
    assert approvals[-1].payload["decision"] == "cancel"
    await manager.close()


async def test_unknown_server_request_is_surfaced_then_method_rejected() -> None:

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.send(
            {
                "method": "future/unsafeRequest",
                "id": 91,
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "itemId": "future-1",
                },
            }
        )
        answer = yield Step.recv()
        assert answer["id"] == 91
        assert answer["error"]["code"] == -32601
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "future request")
    await asyncio.sleep(0.05)
    approval = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )
    assert approval.payload["approval_kind"] == "unknown"
    assert approval.payload["auto_resolved"] is True
    assert approval.payload["decision"] == "unsupported"
    await manager.close()
