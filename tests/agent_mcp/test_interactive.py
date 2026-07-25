from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from trowel_py.agent_mcp.interactive import (
    InteractiveBroker,
    InteractiveDelegationError,
)


def _sse(event: dict[str, object]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


def _create_body(tmp_path: Path) -> dict[str, object]:
    return {
        "runtime": "claude_code",
        "workdir": str(tmp_path),
        "permission_mode": "bypassPermissions",
        "session_kind": "delegate",
        "memory_eligibility": False,
        "agent_mcp_enabled": False,
        "parent_session_id": "parent-1",
        "delegation_depth": 1,
    }


@pytest.mark.anyio
async def test_guidance_continues_same_live_child_across_tool_calls(
    tmp_path: Path,
) -> None:
    answer_received = asyncio.Event()
    deleted = asyncio.Event()

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(
                {
                    "type": "session_started",
                    "payload": {
                        "cc_session_id": "native-child",
                        "model": "glm-5.1",
                    },
                }
            )
            yield _sse(
                {
                    "type": "elicit_request",
                    "payload": {
                        "tool_use_id": "call-1",
                        "request_id": "request-1",
                        "questions": [{"question": "A or B?"}],
                    },
                }
            )
            await answer_received.wait()
            yield _sse({"type": "text", "payload": {"text": "picked B"}})
            yield _sse({"type": "finished", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(
                    200,
                    json={"data": {"session_id": "child-1"}},
                )
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/answer"):
                assert json.loads(request.content) == {
                    "answers": {"A or B?": "B"},
                    "cancel": False,
                }
                answer_received.set()
                return httpx.Response(200, json={"success": True})
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "session_id": "child-1",
                            "native_session_id": "native-child",
                            "model": "glm-5.2",
                        }
                    },
                )
            if request.method == "DELETE":
                deleted.set()
                return httpx.Response(200, json={"success": True})
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
    )

    first = await broker.start(
        parent_session_id="parent-1",
        task="ask before continuing",
        create_body=_create_body(tmp_path),
    )

    assert first["status"] == "needs_guidance"
    assert first["child"]["native_session_id"] == "native-child"
    assert first["child"]["model"] == "glm-5.1"
    assert first["needs_guidance"]["request_id"] == "request-1"
    assert first["cleanup"] == {"status": "pending", "error": None}

    second = await broker.respond(
        first["delegation_id"], {"A or B?": "B"}
    )

    assert second["status"] == "completed"
    assert second["child"]["native_session_id"] == "native-child"
    assert second["child"]["model"] == "glm-5.2"
    assert second["reported"]["answer"] == "picked B"
    assert second["observed"]["terminal_event"] == "finished"
    assert second["observed"]["event_counts"]["elicit_request"] == 1

    closed = await broker.close(first["delegation_id"])
    assert closed["status"] == "closed"
    assert closed["cleanup"] == {"status": "deleted", "error": None}
    assert deleted.is_set()


@pytest.mark.anyio
async def test_rejected_answer_preserves_pending_guidance(tmp_path: Path) -> None:
    question = {
        "tool_use_id": "call-1",
        "request_id": "request-1",
        "questions": [{"question": "A or B?"}],
    }

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse({"type": "elicit_request", "payload": question})
            await asyncio.Event().wait()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-2"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/answer"):
                return httpx.Response(200, json={"success": False})
            if request.url.path.endswith("/interrupt"):
                return httpx.Response(200)
            if request.method == "DELETE":
                return httpx.Response(200)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
        consumer_stop_timeout=0.01,
    )
    first = await broker.start(
        parent_session_id="parent-1",
        task="ask",
        create_body=_create_body(tmp_path),
    )

    with pytest.raises(
        InteractiveDelegationError,
        match="rejected the elicitation answer",
    ):
        await broker.respond(first["delegation_id"], {"A or B?": "B"})

    status = broker.status(first["delegation_id"])
    assert status["status"] == "needs_guidance"
    assert status["needs_guidance"] == question
    assert status["error"] == "CC rejected the elicitation answer"
    await broker.close(first["delegation_id"])


@pytest.mark.anyio
async def test_close_interrupts_active_child_before_delete(tmp_path: Path) -> None:
    interrupted = asyncio.Event()
    requests: list[tuple[str, str]] = []

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(
                {
                    "type": "elicit_request",
                    "payload": {
                        "request_id": "request-1",
                        "questions": [{"question": "Continue?"}],
                    },
                }
            )
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-3"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/interrupt"):
                interrupted.set()
                return httpx.Response(200)
            if request.method == "DELETE":
                return httpx.Response(200)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
    )
    first = await broker.start(
        parent_session_id="parent-1",
        task="ask",
        create_body=_create_body(tmp_path),
    )

    closed = await broker.close(first["delegation_id"])

    assert closed["status"] == "closed"
    assert requests[-2:] == [
        ("POST", "/api/agent/sessions/child-3/interrupt"),
        ("DELETE", "/api/agent/sessions/child-3"),
    ]


@pytest.mark.anyio
async def test_close_interrupts_failed_stream_without_terminal_before_delete(
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, str]] = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-4"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(503)
            if request.url.path.endswith("/interrupt"):
                return httpx.Response(200)
            if request.method == "DELETE":
                return httpx.Response(200)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
    )
    first = await broker.start(
        parent_session_id="parent-1",
        task="fail before terminal",
        create_body=_create_body(tmp_path),
    )
    assert first["status"] == "failed"
    assert first["observed"]["terminal_event"] is None

    await broker.close(first["delegation_id"])

    assert requests[-2:] == [
        ("POST", "/api/agent/sessions/child-4/interrupt"),
        ("DELETE", "/api/agent/sessions/child-4"),
    ]


@pytest.mark.anyio
async def test_delete_retry_does_not_interrupt_completed_child(tmp_path: Path) -> None:
    delete_attempts = 0

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal delete_attempts
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-5"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(
                    200,
                    content=_sse({"type": "finished", "payload": {}}),
                )
            if request.method == "GET":
                return httpx.Response(200, json={"data": {"session_id": "child-5"}})
            if request.url.path.endswith("/interrupt"):
                raise AssertionError("completed child must not be interrupted")
            if request.method == "DELETE":
                delete_attempts += 1
                return httpx.Response(503 if delete_attempts == 1 else 200)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
    )
    first = await broker.start(
        parent_session_id="parent-1",
        task="complete",
        create_body=_create_body(tmp_path),
    )
    assert first["status"] == "completed"

    pending = await broker.close(first["delegation_id"])
    assert pending["status"] == "cleanup_pending"
    assert pending["cleanup"]["status"] == "preserved"

    closed = await broker.close(first["delegation_id"])
    assert closed["status"] == "closed"
    assert delete_attempts == 2


@pytest.mark.anyio
async def test_cancelled_start_interrupts_and_deletes_started_child(
    tmp_path: Path,
) -> None:
    stream_started = asyncio.Event()
    interrupted = asyncio.Event()
    deleted = asyncio.Event()

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            stream_started.set()
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-6"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/interrupt"):
                interrupted.set()
                return httpx.Response(200)
            if request.method == "DELETE":
                deleted.set()
                return httpx.Response(200)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
    )
    start = asyncio.create_task(
        broker.start(
            parent_session_id="parent-1",
            task="wait without asking",
            create_body=_create_body(tmp_path),
        )
    )
    await stream_started.wait()

    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start

    assert interrupted.is_set()
    assert deleted.is_set()


@pytest.mark.anyio
async def test_interrupt_failure_preserves_child_until_retry(tmp_path: Path) -> None:
    interrupted = asyncio.Event()
    interrupt_attempts = 0
    delete_attempts = 0

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(
                {
                    "type": "elicit_request",
                    "payload": {
                        "request_id": "request-1",
                        "questions": [{"question": "Continue?"}],
                    },
                }
            )
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal interrupt_attempts, delete_attempts
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-7"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/interrupt"):
                interrupt_attempts += 1
                if interrupt_attempts == 1:
                    return httpx.Response(503)
                interrupted.set()
                return httpx.Response(200)
            if request.method == "DELETE":
                delete_attempts += 1
                return httpx.Response(200)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
    )
    first = await broker.start(
        parent_session_id="parent-1",
        task="ask",
        create_body=_create_body(tmp_path),
    )

    with pytest.raises(
        InteractiveDelegationError,
        match="binding child-7 was preserved",
    ):
        await broker.close(first["delegation_id"])

    preserved = broker.status(first["delegation_id"])
    assert preserved["status"] == "unknown_requires_reconcile"
    assert preserved["cleanup"]["status"] == "preserved"
    assert delete_attempts == 0

    closed = await broker.close(first["delegation_id"])
    assert closed["status"] == "closed"
    assert delete_attempts == 1


@pytest.mark.anyio
async def test_cleanup_timeout_marks_state_unknown(tmp_path: Path) -> None:
    delete_started = asyncio.Event()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-8"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(
                    200,
                    content=_sse({"type": "finished", "payload": {}}),
                )
            if request.method == "GET":
                return httpx.Response(200, json={"data": {"session_id": "child-8"}})
            if request.method == "DELETE":
                delete_started.set()
                await asyncio.Event().wait()
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
        cleanup_timeout=0.01,
        consumer_stop_timeout=0.01,
    )
    first = await broker.start(
        parent_session_id="parent-1",
        task="complete",
        create_body=_create_body(tmp_path),
    )

    with pytest.raises(
        InteractiveDelegationError,
        match="cleanup timed out",
    ):
        await broker.close(first["delegation_id"])

    assert delete_started.is_set()
    status = broker.status(first["delegation_id"])
    assert status["status"] == "unknown_requires_reconcile"
    assert status["cleanup"]["status"] == "preserved"
