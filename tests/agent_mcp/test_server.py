from __future__ import annotations

import json
import asyncio
from pathlib import Path

import httpx
import mcp.types as types
import pytest

from trowel_py.agent_mcp import server


def _context(workdir: Path) -> server.ParentContext:
    return server.ParentContext(
        session_id="parent-1",
        runtime="codex",
        workdir=str(workdir),
        permission="danger-full-access",
        memory_enabled=True,
        profile_enabled=False,
        self_enabled=True,
        delegation_depth=0,
    )


def _parent_binding(workdir: Path, **changes: object) -> dict[str, object]:
    binding: dict[str, object] = {
        "session_id": "parent-1",
        "runtime": "codex",
        "workdir": str(workdir),
        "effective_sandbox": "danger-full-access",
        "effective_approval": "never",
        "network_access": True,
        "memory_enabled": True,
        "profile_enabled": False,
        "self_enabled": True,
        "session_kind": "user",
        "agent_mcp_enabled": True,
        "delegation_depth": 0,
    }
    binding.update(changes)
    return binding


def _sse(*events: dict[str, object]) -> bytes:
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)


def _request(
    name: str, arguments: dict[str, object] | None = None
) -> types.CallToolRequest:
    return types.CallToolRequest(
        params=types.CallToolRequestParams(name=name, arguments=arguments)
    )


def _tool_payload(result: types.ServerResult) -> dict[str, object]:
    content = result.root.content
    assert len(content) == 1
    assert isinstance(content[0], types.TextContent)
    return json.loads(content[0].text)


def test_tool_does_not_accept_authority_fields() -> None:
    schema = server._tool().inputSchema
    assert schema["required"] == ["target_runtime", "task"]
    for field in ("workdir", "permission", "parent_session_id", "delegation_depth"):
        assert field not in schema["properties"]


def test_interactive_tools_only_publish_verified_claude_guidance() -> None:
    tools = {tool.name: tool for tool in server._tools()}
    assert set(tools) == {
        "delegate",
        "delegate_start",
        "delegate_respond",
        "delegate_status",
        "delegate_close",
    }
    assert all(
        tool.meta == {"anthropic/alwaysLoad": True} for tool in tools.values()
    )
    start_runtime = tools["delegate_start"].inputSchema["properties"]["target_runtime"]
    assert start_runtime["enum"] == ["claude_code"]
    assert "immediately return" in tools["delegate_start"].description
    assert "without polling" in tools["delegate_start"].description
    assert "automatically starts a parent turn" in tools["delegate_start"].description
    assert "several minutes" in tools["delegate"].description
    status_properties = tools["delegate_status"].inputSchema["properties"]
    assert status_properties == {"delegation_id": {"type": "string", "minLength": 1}}
    for tool_name in ("delegate_start", "delegate_respond"):
        properties = tools[tool_name].inputSchema["properties"]
        for field in (
            "workdir",
            "permission",
            "parent_session_id",
            "delegation_depth",
        ):
            assert field not in properties


def test_agent_host_headers_use_desktop_process_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """桌面 Agent MCP 使用既有私有进程凭据访问 Agent Host。"""

    monkeypatch.setenv("TROWEL_RESOURCE_REGISTRATION_CREDENTIAL", "desktop-secret")

    assert server._agent_host_headers() == {"Authorization": "Bearer desktop-secret"}


@pytest.mark.anyio
async def test_delegate_reports_connection_capacity_detail(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/parent-1"):
            return httpx.Response(200, json={"data": _parent_binding(tmp_path)})
        if request.url.path == "/api/agent/sessions":
            return httpx.Response(
                409,
                json={"detail": "当前委派数量已满：连接上限为 5"},
            )
        raise AssertionError(request.url)

    async with httpx.AsyncClient(
        base_url="http://trowel.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            server.DelegationError,
            match="当前委派数量已满：连接上限为 5",
        ):
            await server.delegate_agent(
                client,
                context=_context(tmp_path),
                runtime="claude_code",
                task="work",
            )


@pytest.mark.anyio
async def test_delegate_reports_running_capacity_detail(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/parent-1"):
            return httpx.Response(200, json={"data": _parent_binding(tmp_path)})
        if request.url.path == "/api/agent/sessions":
            return httpx.Response(200, json={"data": {"session_id": "child-full"}})
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                content=_sse(
                    {
                        "type": "error",
                        "payload": {"errors": ["当前委派数量已满：同时在跑上限为 5"]},
                    }
                ),
            )
        if request.method == "DELETE":
            return httpx.Response(200)
        raise AssertionError(request.url)

    async with httpx.AsyncClient(
        base_url="http://trowel.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            server.DelegationError,
            match="当前委派数量已满：同时在跑上限为 5",
        ):
            await server.delegate_agent(
                client,
                context=_context(tmp_path),
                runtime="codex",
                task="work",
            )


@pytest.mark.anyio
async def test_mcp_dispatch_revalidates_parent_before_responding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    verified: list[str] = []

    class Broker:
        async def respond(
            self,
            delegation_id: str,
            answers: dict[str, str],
            *,
            parent_session_id: str,
        ) -> dict[str, object]:
            assert delegation_id == "delegation-1"
            assert answers == {"A or B?": "B"}
            assert parent_session_id == "parent-1"
            return {"delegation_id": delegation_id, "status": "completed"}

    async def verify(
        _client: httpx.AsyncClient, supplied: server.ParentContext
    ) -> server.ParentContext:
        verified.append(supplied.session_id)
        return supplied

    monkeypatch.setattr(server, "_parent_context", lambda: context)
    monkeypatch.setattr(server, "_server_base_url", lambda: "http://trowel.test")
    monkeypatch.setattr(server, "_verified_parent_context", verify)
    handler = server._build_server(Broker()).request_handlers[types.CallToolRequest]

    result = await handler(
        _request(
            "delegate_respond",
            {
                "delegation_id": "delegation-1",
                "answers": {"A or B?": "B"},
            },
        )
    )

    assert _tool_payload(result) == {
        "delegation_id": "delegation-1",
        "status": "completed",
    }
    assert verified == ["parent-1"]


@pytest.mark.anyio
async def test_mcp_dispatch_reads_status_without_waiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状态工具同步读取 broker 快照，不建立阻塞等待。"""

    context = _context(tmp_path)

    class Broker:
        async def status(
            self,
            delegation_id: str,
            *,
            parent_session_id: str,
        ) -> dict[str, object]:
            assert delegation_id == "delegation-1"
            assert parent_session_id == "parent-1"
            return {
                "delegation_id": delegation_id,
                "version": 8,
                "status": "completed",
            }

    monkeypatch.setattr(server, "_parent_context", lambda: context)
    handler = server._build_server(Broker()).request_handlers[types.CallToolRequest]

    result = await handler(
        _request(
            "delegate_status",
            {"delegation_id": "delegation-1"},
        )
    )

    assert _tool_payload(result) == {
        "delegation_id": "delegation-1",
        "version": 8,
        "status": "completed",
    }


def test_parent_context_fails_closed_for_non_full_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TROWEL_PARENT_SESSION_ID", "parent")
    monkeypatch.setenv("TROWEL_PARENT_RUNTIME", "codex")
    monkeypatch.setenv("TROWEL_PARENT_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TROWEL_PARENT_PERMISSION", "workspace-write")
    with pytest.raises(server.DelegationError, match="requires 'danger-full-access'"):
        server._parent_context()


@pytest.mark.anyio
async def test_delegate_inherits_parent_facts_and_cleans_up(tmp_path: Path) -> None:
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path.endswith("/parent-1"):
            return httpx.Response(
                200,
                json={
                    "data": _parent_binding(
                        tmp_path,
                        memory_enabled=False,
                        profile_enabled=True,
                        self_enabled=False,
                    )
                },
            )
        if request.url.path == "/api/agent/sessions":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "session_id": "child-1",
                        "runtime": "claude_code",
                        "model": "glm-5.1",
                    },
                },
            )
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                content=_sse(
                    {"type": "text", "payload": {"text": "done"}},
                    {"type": "finished", "payload": {}},
                ),
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "session_id": "child-1",
                        "native_session_id": "native-1",
                        "model": "glm-5.1",
                    },
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={"success": True})
        raise AssertionError(request.url)

    async with httpx.AsyncClient(
        base_url="http://trowel.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await server.delegate_agent(
            client,
            context=_context(tmp_path),
            runtime="claude_code",
            task="implement the change",
        )

    create = next(
        body
        for method, path, body in requests
        if method == "POST" and path == "/api/agent/sessions"
    )
    assert isinstance(create, dict)
    assert create["workdir"] == str(tmp_path)
    assert create["permission_mode"] == "bypassPermissions"
    assert create["session_kind"] == "delegate"
    assert create["memory_enabled"] is False
    assert create["profile_enabled"] is True
    assert create["self_enabled"] is False
    assert create["memory_eligibility"] is False
    assert create["agent_mcp_enabled"] is False
    assert create["parent_session_id"] == "parent-1"
    assert create["delegation_depth"] == 1
    assert result.answer == "done"
    assert result.child_binding["native_session_id"] == "native-1"
    assert result.to_dict()["observed"]["event_counts"] == {
        "text": 1,
        "finished": 1,
    }
    assert requests[-1][:2] == ("DELETE", "/api/agent/sessions/child-1")


@pytest.mark.anyio
async def test_interrupt_failure_preserves_binding(tmp_path: Path) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/parent-1"):
            return httpx.Response(200, json={"data": _parent_binding(tmp_path)})
        if request.url.path == "/api/agent/sessions":
            return httpx.Response(200, json={"data": {"session_id": "child-2"}})
        if request.url.path.endswith("/messages"):
            return httpx.Response(503)
        if request.url.path.endswith("/interrupt"):
            return httpx.Response(503)
        if request.method == "DELETE":
            raise AssertionError("must not delete after failed interrupt")
        raise AssertionError(request.url)

    async with httpx.AsyncClient(
        base_url="http://trowel.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(server.DelegationCleanupError, match="was preserved"):
            await server.delegate_agent(
                client,
                context=_context(tmp_path),
                runtime="codex",
                task="wait",
            )
    assert requests[-1] == ("POST", "/api/agent/sessions/child-2/interrupt")


@pytest.mark.anyio
async def test_cancellation_waits_for_interrupt_and_delete(tmp_path: Path) -> None:
    requests: list[tuple[str, str]] = []
    stream_started = asyncio.Event()
    delete_finished = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            stream_started.set()
            await asyncio.Event().wait()
            yield b""

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.method == "GET" and request.url.path.endswith("/parent-1"):
                return httpx.Response(200, json={"data": _parent_binding(tmp_path)})
            if request.url.path == "/api/agent/sessions":
                return httpx.Response(200, json={"data": {"session_id": "child-3"}})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, stream=BlockingStream())
            if request.url.path.endswith("/interrupt"):
                return httpx.Response(200)
            if request.method == "DELETE":
                await asyncio.sleep(0.01)
                delete_finished.set()
                return httpx.Response(200)
            raise AssertionError(request.url)

    async with httpx.AsyncClient(
        base_url="http://trowel.test", transport=Transport()
    ) as client:
        call = asyncio.create_task(
            server.delegate_agent(
                client,
                context=_context(tmp_path),
                runtime="codex",
                task="wait",
            )
        )
        await stream_started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    assert delete_finished.is_set()
    assert requests[-2:] == [
        ("POST", "/api/agent/sessions/child-3/interrupt"),
        ("DELETE", "/api/agent/sessions/child-3"),
    ]


@pytest.mark.anyio
async def test_delete_failure_preserves_successful_answer(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/parent-1"):
            return httpx.Response(200, json={"data": _parent_binding(tmp_path)})
        if request.url.path == "/api/agent/sessions":
            return httpx.Response(200, json={"data": {"session_id": "child-4"}})
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                content=_sse(
                    {"type": "text", "payload": {"text": "answer"}},
                    {"type": "finished", "payload": {}},
                ),
            )
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"session_id": "child-4"}})
        if request.method == "DELETE":
            return httpx.Response(503)
        raise AssertionError(request.url)

    async with httpx.AsyncClient(
        base_url="http://trowel.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await server.delegate_agent(
            client,
            context=_context(tmp_path),
            runtime="claude_code",
            task="work",
        )

    assert result.answer == "answer"
    assert result.cleanup_status == "preserved"
    assert result.cleanup_error is not None
    assert result.to_dict()["status"] == "cleanup_pending"


@pytest.mark.anyio
async def test_delegate_rejects_when_effective_parent_permission_was_downgraded(
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/parent-1"):
            return httpx.Response(
                200,
                json={
                    "data": _parent_binding(
                        tmp_path,
                        effective_sandbox="workspace-write",
                        effective_approval="on-request",
                    )
                },
            )
        raise AssertionError(
            "child session must not be created after permission downgrade"
        )

    async with httpx.AsyncClient(
        base_url="http://trowel.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(server.DelegationError, match="effective parent permission"):
            await server.delegate_agent(
                client,
                context=_context(tmp_path),
                runtime="claude_code",
                task="work",
            )
    assert requests == [("GET", "/api/agent/sessions/parent-1")]


@pytest.mark.anyio
async def test_verified_cc_parent_requires_bypass_permissions(tmp_path: Path) -> None:
    context = server.ParentContext(
        session_id="parent-cc",
        runtime="claude_code",
        workdir=str(tmp_path),
        permission="bypassPermissions",
        memory_enabled=True,
        profile_enabled=True,
        self_enabled=True,
        delegation_depth=0,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agent/sessions/parent-cc"
        return httpx.Response(
            200,
            json={
                "data": {
                    "session_id": "parent-cc",
                    "runtime": "claude_code",
                    "workdir": str(tmp_path),
                    "permission": "default",
                    "memory_enabled": True,
                    "profile_enabled": True,
                    "self_enabled": True,
                    "session_kind": "user",
                    "agent_mcp_enabled": True,
                    "delegation_depth": 0,
                }
            },
        )

    async with httpx.AsyncClient(
        base_url="http://trowel.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(server.DelegationError, match="effective parent permission"):
            await server._verified_parent_context(client, context)
