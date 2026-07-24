from __future__ import annotations

import asyncio

from trowel_py.codex_host.errors import ServerRequestUnsupportedError
from tests.codex_host._fake import Step
from tests.codex_host.transport.support import _build, _initialize_response


async def test_notification_dispatches_to_listeners() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send({"method": "item/completed", "params": {"threadId": "t"}})
        yield Step.send({"method": "turn/completed", "params": {"turn": {"id": "u"}}})
        yield Step.recv()

    client, _fake = _build(behavior())
    seen: list[tuple[str, dict]] = []
    client.add_notification_listener(lambda m, p: seen.append((m, p)))
    await client.start()
    await asyncio.sleep(0.05)  # 等待 reader 分发 notification。
    methods = [m for m, _ in seen]
    assert "item/completed" in methods
    assert "turn/completed" in methods
    await client.close()


async def test_registered_server_request_handler_replies_result() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 7,
                "params": {"command": "rm -rf /", "cwd": "/"},
            }
        )
        yield Step.recv()
        yield Step.recv()

    client, fake = _build(behavior())

    async def approve(request_id: object, _method: str, _params: dict) -> dict:
        assert request_id == 7
        return {"decision": "decline"}

    client.register_server_request_handler(
        "item/commandExecution/requestApproval", approve
    )
    await client.start()
    await asyncio.sleep(0.05)  # 等待异步 handler 写回响应。
    replies = [r for r in fake.received if r.get("id") == 7]
    assert len(replies) == 1
    assert replies[0]["result"] == {"decision": "decline"}
    await client.close()


async def test_unsupported_server_request_is_rejected_not_auto_approved() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 42,
                "params": {"command": "evil"},
            }
        )
        yield Step.recv()
        yield Step.recv()

    client, fake = _build(behavior())
    await client.start()
    await asyncio.sleep(0.05)
    replies = [r for r in fake.received if r.get("id") == 42]
    assert len(replies) == 1
    assert "error" in replies[0]
    assert replies[0]["error"]["code"] == -32601
    assert "result" not in replies[0]
    await client.close()


async def test_handler_refusal_returns_one_error_no_fallthrough() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 5,
                "params": {"command": "x"},
            }
        )
        yield Step.recv()
        yield Step.recv()

    client, fake = _build(behavior())

    async def refuse(request_id: object, _method: str, _params: dict) -> dict:
        raise ServerRequestUnsupportedError(
            "item/commandExecution/requestApproval", request_id
        )

    client.register_server_request_handler(
        "item/commandExecution/requestApproval", refuse
    )
    await client.start()
    await asyncio.sleep(0.05)
    replies = [r for r in fake.received if r.get("id") == 5]
    assert len(replies) == 1
    assert replies[0]["error"]["code"] == -32601
    assert "result" not in replies[0]
    await client.close()
