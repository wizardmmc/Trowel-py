from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from trowel_py.agent_mcp.interactive import (
    InteractiveBroker,
    _normalize_answers,
)
from trowel_py.agent_mcp.interactive_client import InteractiveBrokerClient
from trowel_py.agent_mcp.interactive_errors import InteractiveDelegationError


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
async def test_broker_client_recovers_handle_through_agent_host() -> None:
    """新 MCP 进程可以通过 Agent Host 读取上一进程创建的句柄。"""

    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer desktop-secret"
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "delegation_id": "delegation-1",
                        "status": "starting",
                        "version": 0,
                    },
                    "error": None,
                },
            )
        assert request.url.params["parent_session_id"] == "parent-1"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "delegation_id": "delegation-1",
                    "status": "completed",
                    "version": 2,
                },
                "error": None,
            },
        )

    transport = httpx.MockTransport(handler)
    first_process = InteractiveBrokerClient(
        base_url="http://trowel.test",
        transport=transport,
        headers={"Authorization": "Bearer desktop-secret"},
    )
    second_process = InteractiveBrokerClient(
        base_url="http://trowel.test",
        transport=transport,
        headers={"Authorization": "Bearer desktop-secret"},
    )

    started = await first_process.start(
        parent_session_id="parent-1",
        task="long work",
        create_body={"runtime": "claude_code"},
    )
    completed = await second_process.status(
        str(started["delegation_id"]),
        parent_session_id="parent-1",
    )

    assert completed["status"] == "completed"
    assert requests == [
        ("POST", "/api/agent/internal/delegations"),
        ("GET", "/api/agent/internal/delegations/delegation-1"),
    ]


async def _wait_for_status(
    broker: InteractiveBroker,
    delegation_id: str,
    expected: str,
) -> dict[str, object]:
    """测试内部等待后台 consumer 推进委派状态。"""

    snapshot = broker.status(delegation_id)
    for _ in range(100):
        if snapshot["status"] == expected:
            return snapshot
        await asyncio.sleep(0.01)
        snapshot = broker.status(delegation_id)
    raise AssertionError(f"delegation did not reach {expected}: {snapshot}")


@pytest.mark.anyio
async def test_start_returns_before_child_becomes_actionable(tmp_path: Path) -> None:
    """启动只登记后台委派，不占住父模型直到 child 结束。"""

    create_allowed = asyncio.Event()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                await create_allowed.wait()
                return httpx.Response(503)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
        consumer_stop_timeout=0.01,
    )
    started = await asyncio.wait_for(
        broker.start(
            parent_session_id="parent-1",
            task="work for a long time",
            create_body=_create_body(tmp_path),
        ),
        timeout=0.1,
    )

    assert started["status"] == "starting"
    assert started["version"] == 0
    create_allowed.set()
    await _wait_for_status(broker, str(started["delegation_id"]), "failed")
    await broker.close(str(started["delegation_id"]))


@pytest.mark.anyio
async def test_status_returns_immediately_while_child_runs(tmp_path: Path) -> None:
    """查询只读当前事实，不用阻塞工具调用占住父模型。"""

    create_allowed = asyncio.Event()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                await create_allowed.wait()
                return httpx.Response(503)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
        consumer_stop_timeout=0.01,
    )
    started = await broker.start(
        parent_session_id="parent-1",
        task="work",
        create_body=_create_body(tmp_path),
    )

    unchanged = broker.status(str(started["delegation_id"]))

    assert unchanged["status"] == "starting"
    assert unchanged["version"] == started["version"]
    create_allowed.set()
    await _wait_for_status(broker, str(started["delegation_id"]), "failed")
    await broker.close(str(started["delegation_id"]))


@pytest.mark.anyio
async def test_closed_parent_cannot_register_a_late_delegation(tmp_path: Path) -> None:
    """父关闭边界登记后，迟到的 MCP start 不能留下无 owner child。"""

    broker = InteractiveBroker(base_url="http://trowel.test")

    await broker.close_parent("parent-1")

    with pytest.raises(InteractiveDelegationError, match="closing or closed"):
        await broker.start(
            parent_session_id="parent-1",
            task="late work",
            create_body=_create_body(tmp_path),
        )


def test_guidance_answer_header_is_normalized_to_full_question() -> None:
    """Claude Code 接口始终收到完整问题文本作为答案键。"""

    pending = {"questions": [{"header": "Choice", "question": "Choose A or B?"}]}

    assert _normalize_answers(pending, {"Choice": "B"}) == {"Choose A or B?": "B"}


@pytest.mark.anyio
async def test_close_waits_for_create_binding_before_cleanup(tmp_path: Path) -> None:
    """创建尚在返回时，关闭要等到取得 child ID 后再中断和删除。"""

    create_allowed = asyncio.Event()
    interrupted = asyncio.Event()
    deleted = asyncio.Event()

    class ChildStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await interrupted.wait()
            yield _sse({"type": "interrupted", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                await create_allowed.wait()
                return httpx.Response(200, json={"data": {"session_id": "child-late"}})
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
        cleanup_timeout=0.1,
    )
    started = await broker.start(
        parent_session_id="parent-1",
        task="work",
        create_body=_create_body(tmp_path),
    )
    closing = asyncio.create_task(broker.close(str(started["delegation_id"])))
    await asyncio.sleep(0)
    assert not closing.done()

    create_allowed.set()
    closed = await closing

    assert closed["status"] == "closed"
    assert interrupted.is_set()
    assert deleted.is_set()


@pytest.mark.anyio
async def test_start_reports_connection_capacity_detail(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agent/sessions"
        return httpx.Response(
            409,
            json={"detail": "当前委派数量已满：连接上限为 5"},
        )

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=httpx.MockTransport(handler),
    )

    started = await broker.start(
        parent_session_id="parent-1",
        task="work",
        create_body=_create_body(tmp_path),
    )
    result = await _wait_for_status(broker, str(started["delegation_id"]), "failed")

    assert result["status"] == "failed"
    assert result["error"] == "当前委派数量已满：连接上限为 5"


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
                        "questions": [{"header": "Choice", "question": "A or B?"}],
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
        broker, str(started["delegation_id"]), "needs_guidance"
    )

    assert first["status"] == "needs_guidance"
    assert first["child"]["native_session_id"] == "native-child"
    assert first["child"]["model"] == "glm-5.1"
    assert first["needs_guidance"]["request_id"] == "request-1"
    assert first["cleanup"] == {"status": "pending", "error": None}

    second = await broker.respond(first["delegation_id"], {"Choice": "B"})

    assert second["status"] == "running"
    finish_allowed.set()
    completed = await _wait_for_status(broker, str(first["delegation_id"]), "completed")
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
        broker, str(started["delegation_id"]), "needs_guidance"
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
async def test_broker_notifies_parent_for_guidance_and_completion(
    tmp_path: Path,
) -> None:
    """broker 把每个可执行状态版本主动交给父会话调度器。"""

    answer_received = asyncio.Event()
    finish_allowed = asyncio.Event()
    notices: list[dict[str, object]] = []

    async def notify(snapshot: dict[str, object]) -> None:
        notices.append(snapshot)

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
            await answer_received.wait()
            await finish_allowed.wait()
            yield _sse({"type": "text", "payload": {"text": "done"}})
            yield _sse({"type": "finished", "payload": {}})

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-n"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=ChildStream())
            if request.url.path.endswith("/answer"):
                answer_received.set()
                return httpx.Response(200, json={"success": True})
            if request.method == "GET":
                return httpx.Response(200, json={"data": {"session_id": "child-n"}})
            if request.method == "DELETE":
                return httpx.Response(200)
            raise AssertionError((request.method, request.url.path))

    broker = InteractiveBroker(
        base_url="http://trowel.test",
        transport=Transport(),
        notifier=notify,
    )
    started = await broker.start(
        parent_session_id="parent-1",
        task="ask and finish",
        create_body=_create_body(tmp_path),
    )
    waiting = await _wait_for_status(
        broker, str(started["delegation_id"]), "needs_guidance"
    )

    assert [(item["status"], item["version"]) for item in notices] == [
        ("needs_guidance", waiting["version"])
    ]

    await broker.respond(str(started["delegation_id"]), {"Continue?": "yes"})
    finish_allowed.set()
    completed = await _wait_for_status(
        broker, str(started["delegation_id"]), "completed"
    )

    assert [(item["status"], item["version"]) for item in notices] == [
        ("needs_guidance", waiting["version"]),
        ("completed", completed["version"]),
    ]
    await broker.close(str(started["delegation_id"]))


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
    started = await broker.start(
        parent_session_id="parent-1",
        task="fail before terminal",
        create_body=_create_body(tmp_path),
    )
    first = await _wait_for_status(broker, str(started["delegation_id"]), "failed")
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
    first = await _wait_for_status(broker, str(started["delegation_id"]), "completed")
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
