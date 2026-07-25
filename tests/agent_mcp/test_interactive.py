from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from trowel_py.agent_mcp.interactive import (
    InteractiveBroker,
    InteractiveDelegationError,
    _normalize_answers,
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


async def _wait_for_status(
    broker: InteractiveBroker,
    delegation_id: str,
    *expected: str,
) -> dict[str, object]:
    async def wait() -> dict[str, object]:
        snapshot = broker.status(delegation_id)
        while snapshot["status"] not in expected:
            snapshot = await broker.wait_status(
                delegation_id,
                after_version=int(snapshot["version"]),
                wait_seconds=0.1,
            )
        return snapshot

    return await asyncio.wait_for(wait(), timeout=1)


@pytest.mark.anyio
async def test_start_returns_handle_before_child_becomes_actionable(
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
                return httpx.Response(200, json={"data": {"session_id": "child-slow"}})
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

    started = await asyncio.wait_for(
        broker.start(
            parent_session_id="parent-1",
            task="work longer than the MCP tool timeout",
            create_body=_create_body(tmp_path),
        ),
        timeout=0.1,
    )

    assert started["delegation_id"]
    assert started["status"] in {"starting", "running"}
    await stream_started.wait()
    await broker.close(started["delegation_id"])
    assert deleted.is_set()


@pytest.mark.anyio
async def test_close_waits_for_starting_child_then_interrupts_before_delete(
    tmp_path: Path,
) -> None:
    create_started = asyncio.Event()
    allow_create = asyncio.Event()
    interrupted = asyncio.Event()
    requests: list[tuple[str, str]] = []

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.path == "/api/agent/sessions":
                create_started.set()
                await allow_create.wait()
                return httpx.Response(200, json={"data": {"session_id": "child-race"}})
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
        cleanup_timeout=0.1,
        consumer_stop_timeout=0.01,
    )
    started = await broker.start(
        parent_session_id="parent-1",
        task="close while the child binding is being created",
        create_body=_create_body(tmp_path),
    )
    await create_started.wait()

    close = asyncio.create_task(broker.close(started["delegation_id"]))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not close.done()
    allow_create.set()
    await close

    assert requests[-2:] == [
        ("POST", "/api/agent/sessions/child-race/interrupt"),
        ("DELETE", "/api/agent/sessions/child-race"),
    ]


@pytest.mark.anyio
async def test_unresolved_start_is_preserved_until_child_id_becomes_known(
    tmp_path: Path,
) -> None:
    create_started = asyncio.Event()
    allow_create = asyncio.Event()
    interrupted = asyncio.Event()

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                create_started.set()
                await allow_create.wait()
                return httpx.Response(200, json={"data": {"session_id": "child-late"}})
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
        cleanup_timeout=0.01,
        consumer_stop_timeout=0.01,
    )
    started = await broker.start(
        parent_session_id="parent-1",
        task="preserve an unresolved child creation",
        create_body=_create_body(tmp_path),
    )
    await create_started.wait()

    with pytest.raises(InteractiveDelegationError, match="did not resolve"):
        await broker.close(started["delegation_id"])
    with pytest.raises(InteractiveDelegationError, match="did not resolve"):
        await broker.close(started["delegation_id"])

    preserved = broker.status(started["delegation_id"])
    assert preserved["status"] == "unknown_requires_reconcile"
    assert preserved["cleanup"]["status"] == "preserved"

    allow_create.set()
    await _wait_for_status(broker, started["delegation_id"], "running")
    closed = await broker.close(started["delegation_id"])
    assert closed["status"] == "closed"


@pytest.mark.anyio
async def test_respond_returns_after_answer_is_accepted_without_waiting_for_child(
    tmp_path: Path,
) -> None:
    answer_received = asyncio.Event()
    interrupted = asyncio.Event()

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(
                {
                    "type": "elicit_request",
                    "payload": {
                        "request_id": "request-slow",
                        "questions": [
                            {"header": "Choice", "question": "Choice?"}
                        ],
                    },
                }
            )
            await answer_received.wait()
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-answer"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/answer"):
                assert json.loads(request.content) == {
                    "answers": {"Choice?": "BETA"},
                    "cancel": False,
                }
                answer_received.set()
                return httpx.Response(200, json={"success": True})
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
    started = await broker.start(
        parent_session_id="parent-1",
        task="ask, then continue for a long time",
        create_body=_create_body(tmp_path),
    )
    while broker.status(started["delegation_id"])["status"] != "needs_guidance":
        await asyncio.sleep(0)

    try:
        responded = await asyncio.wait_for(
            broker.respond(started["delegation_id"], {"Choice": "BETA"}),
            timeout=0.1,
        )
        assert responded["status"] == "running"
        assert responded["delegation_id"] == started["delegation_id"]
    finally:
        await broker.close(started["delegation_id"])


@pytest.mark.anyio
async def test_concurrent_respond_only_answers_pending_question_once(
    tmp_path: Path,
) -> None:
    answer_started = asyncio.Event()
    allow_answer = asyncio.Event()
    interrupted = asyncio.Event()
    answer_calls = 0

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(
                {
                    "type": "elicit_request",
                    "payload": {
                        "request_id": "request-race",
                        "questions": [{"header": "Choice", "question": "Choice?"}],
                    },
                }
            )
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal answer_calls
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-race"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/answer"):
                answer_calls += 1
                answer_started.set()
                await allow_answer.wait()
                return httpx.Response(200, json={"success": True})
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
    started = await broker.start(
        parent_session_id="parent-1",
        task="ask once",
        create_body=_create_body(tmp_path),
    )
    await _wait_for_status(broker, started["delegation_id"], "needs_guidance")

    first = asyncio.create_task(
        broker.respond(started["delegation_id"], {"Choice": "BETA"})
    )
    await answer_started.wait()
    second = asyncio.create_task(
        broker.respond(started["delegation_id"], {"Choice": "BETA"})
    )
    await asyncio.sleep(0)
    allow_answer.set()
    try:
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert answer_calls == 1
        assert sum(isinstance(result, InteractiveDelegationError) for result in results) == 1
    finally:
        await broker.close(started["delegation_id"])


@pytest.mark.parametrize(
    ("questions", "answers", "error"),
    [
        (
            [
                {"header": "Choice", "question": "First choice?"},
                {"header": "Choice", "question": "Second choice?"},
            ],
            {"Choice": "A"},
            "ambiguous guidance answer header",
        ),
        (
            [
                {"header": "First", "question": "First choice?"},
                {"header": "Second", "question": "Second choice?"},
            ],
            {"First": "A"},
            "missing guidance answer",
        ),
        (
            [{"header": "Choice", "question": "Choose?"}],
            {"Choice": "A", "Choose?": "A"},
            "duplicate guidance answer",
        ),
        (
            [{"header": "Choice", "question": "Choose?"}],
            {"Unknown": "A"},
            "unknown guidance answer key",
        ),
        (
            [
                {"header": "First", "question": "Choose?"},
                {"header": "Second", "question": "Choose?"},
            ],
            {"Choose?": "A"},
            "duplicate pending guidance question",
        ),
    ],
)
def test_guidance_answer_keys_fail_closed(
    questions: list[dict[str, str]],
    answers: dict[str, str],
    error: str,
) -> None:
    with pytest.raises(InteractiveDelegationError, match=error):
        _normalize_answers({"questions": questions}, answers)


@pytest.mark.anyio
async def test_guidance_continues_same_live_child_across_tool_calls(
    tmp_path: Path,
) -> None:
    answer_received = asyncio.Event()
    finish_allowed = asyncio.Event()
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
            await finish_allowed.wait()
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

    started = await broker.start(
        parent_session_id="parent-1",
        task="ask before continuing",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(
        broker, started["delegation_id"], "needs_guidance"
    )

    assert first["status"] == "needs_guidance"
    assert first["child"]["native_session_id"] == "native-child"
    assert first["child"]["model"] == "glm-5.1"
    assert first["needs_guidance"]["request_id"] == "request-1"
    assert first["cleanup"] == {"status": "pending", "error": None}

    second = await broker.respond(
        first["delegation_id"], {"A or B?": "B"}
    )

    assert second["status"] == "running"
    finish_allowed.set()
    completed = await _wait_for_status(
        broker, first["delegation_id"], "completed"
    )
    assert completed["child"]["native_session_id"] == "native-child"
    assert completed["child"]["model"] == "glm-5.2"
    assert completed["reported"]["answer"] == "picked B"
    assert completed["observed"]["terminal_event"] == "finished"
    assert completed["observed"]["event_counts"]["elicit_request"] == 1

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
    started = await broker.start(
        parent_session_id="parent-1",
        task="ask",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(
        broker, started["delegation_id"], "needs_guidance"
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
    started = await broker.start(
        parent_session_id="parent-1",
        task="ask",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(
        broker, started["delegation_id"], "needs_guidance"
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
    started = await broker.start(
        parent_session_id="parent-1",
        task="fail before terminal",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(broker, started["delegation_id"], "failed")
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
    started = await broker.start(
        parent_session_id="parent-1",
        task="complete",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(broker, started["delegation_id"], "completed")
    assert first["status"] == "completed"

    pending = await broker.close(first["delegation_id"])
    assert pending["status"] == "cleanup_pending"
    assert pending["cleanup"]["status"] == "preserved"

    closed = await broker.close(first["delegation_id"])
    assert closed["status"] == "closed"
    assert delete_attempts == 2


@pytest.mark.anyio
async def test_shutdown_interrupts_and_deletes_started_child(
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
    await broker.start(
        parent_session_id="parent-1",
        task="wait without asking",
        create_body=_create_body(tmp_path),
    )
    await stream_started.wait()

    await broker.shutdown()

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
    started = await broker.start(
        parent_session_id="parent-1",
        task="ask",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(
        broker, started["delegation_id"], "needs_guidance"
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
    started = await broker.start(
        parent_session_id="parent-1",
        task="complete",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(broker, started["delegation_id"], "completed")

    with pytest.raises(
        InteractiveDelegationError,
        match="cleanup timed out",
    ):
        await broker.close(first["delegation_id"])

    assert delete_started.is_set()
    status = broker.status(first["delegation_id"])
    assert status["status"] == "unknown_requires_reconcile"
    assert status["cleanup"]["status"] == "preserved"
