from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

import httpx
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from trowel_py.agent_mcp import AGENT_MCP_TOOL_NAMES
from trowel_py.agent_mcp.interactive import InteractiveBroker

_SERVER_NAME = "trowel_agents"
_TOOL_DELEGATE = "delegate"
_TOOL_DELEGATE_START = "delegate_start"
_TOOL_DELEGATE_RESPOND = "delegate_respond"
_TOOL_DELEGATE_STATUS = "delegate_status"
_TOOL_DELEGATE_CLOSE = "delegate_close"
_SUCCESS_TERMINALS = frozenset({"finished"})
_ERROR_TERMINALS = frozenset({"error", "interrupted", "session_exited"})
_ALL_TERMINALS = _SUCCESS_TERMINALS | _ERROR_TERMINALS

logger = logging.getLogger(__name__)
T = TypeVar("T")


class DelegationError(RuntimeError):
    pass


class DelegationCleanupError(DelegationError):
    pass


@dataclass(frozen=True)
class ParentContext:
    session_id: str
    runtime: str
    workdir: str
    permission: str
    memory_enabled: bool
    profile_enabled: bool
    self_enabled: bool
    delegation_depth: int


@dataclass(frozen=True)
class DelegationResult:
    delegation_id: str
    parent_session_id: str
    child_session_id: str
    runtime: str
    answer: str
    terminal_event: str
    event_counts: dict[str, int]
    child_binding: dict[str, Any]
    cleanup_status: str = "deleted"
    cleanup_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "parent": {"trowel_session_id": self.parent_session_id},
            "child": {
                "trowel_session_id": self.child_session_id,
                "runtime": self.runtime,
                "native_session_id": self.child_binding.get("native_session_id"),
                "model": self.child_binding.get("model"),
            },
            "status": (
                "completed" if self.cleanup_status == "deleted" else "cleanup_pending"
            ),
            "observed": {
                "terminal_event": self.terminal_event,
                "event_counts": self.event_counts,
                "changed_paths": None,
                "validation": None,
            },
            "reported": {"answer": self.answer},
            "cleanup": {
                "status": self.cleanup_status,
                "error": self.cleanup_error,
            },
        }


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise DelegationError(f"{name} must be true or false")


def _parent_context() -> ParentContext:
    session_id = os.environ.get("TROWEL_PARENT_SESSION_ID", "").strip()
    runtime = os.environ.get("TROWEL_PARENT_RUNTIME", "").strip()
    workdir_raw = os.environ.get("TROWEL_PARENT_WORKDIR", "").strip()
    permission = os.environ.get("TROWEL_PARENT_PERMISSION", "").strip()
    if not session_id:
        raise DelegationError("TROWEL_PARENT_SESSION_ID is required")
    if runtime not in {"claude_code", "codex"}:
        raise DelegationError(f"unsupported parent runtime: {runtime!r}")
    expected = "bypassPermissions" if runtime == "claude_code" else "danger-full-access"
    if permission != expected:
        raise DelegationError(
            f"delegation is unavailable for parent permission {permission!r}; "
            f"v0 requires {expected!r}"
        )
    workdir = Path(workdir_raw).expanduser().resolve()
    if not workdir.is_dir():
        raise DelegationError(f"parent workdir does not exist: {workdir}")
    try:
        depth = int(os.environ.get("TROWEL_DELEGATION_DEPTH", "0"))
    except ValueError as exc:
        raise DelegationError("TROWEL_DELEGATION_DEPTH must be an integer") from exc
    if depth != 0:
        raise DelegationError("delegate sessions cannot delegate recursively")
    return ParentContext(
        session_id=session_id,
        runtime=runtime,
        workdir=str(workdir),
        permission=permission,
        memory_enabled=_bool_env("TROWEL_PARENT_MEMORY_ENABLED", default=True),
        profile_enabled=_bool_env("TROWEL_PARENT_PROFILE_ENABLED", default=True),
        self_enabled=_bool_env("TROWEL_PARENT_SELF_ENABLED", default=True),
        delegation_depth=depth,
    )


def _server_base_url() -> str:
    raw = os.environ.get("TROWEL_AGENT_BASE_URL", "").strip()
    if not raw:
        raise DelegationError("TROWEL_AGENT_BASE_URL is required")
    url = httpx.URL(raw)
    if url.scheme not in {"http", "https"} or not url.host:
        raise DelegationError(f"invalid Trowel Agent base URL: {raw!r}")
    return str(url).rstrip("/")


def _create_body(
    context: ParentContext,
    *,
    runtime: str,
    model: str | None,
    effort: str | None,
) -> dict[str, Any]:
    if runtime not in {"claude_code", "codex"}:
        raise ValueError(f"unsupported runtime: {runtime}")
    body: dict[str, Any] = {
        "runtime": runtime,
        "workdir": context.workdir,
        "memory_enabled": context.memory_enabled,
        "profile_enabled": context.profile_enabled,
        "self_enabled": context.self_enabled,
        "session_kind": "delegate",
        "memory_eligibility": False,
        "agent_mcp_enabled": False,
        "parent_session_id": context.session_id,
        "delegation_depth": 1,
    }
    if runtime == "codex":
        body["permission_preset"] = "danger-full-access"
    else:
        body["permission_mode"] = "bypassPermissions"
    if model:
        body["model"] = model
    if effort:
        body["effort"] = effort
    return body


def _binding_bool(data: dict[str, Any], field: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise DelegationError(f"parent binding has invalid {field}")
    return value


async def _verified_parent_context(
    client: httpx.AsyncClient,
    context: ParentContext,
) -> ParentContext:
    response = await client.get(f"/api/agent/sessions/{context.session_id}")
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise DelegationError("parent session response has no data binding")

    if data.get("session_id") != context.session_id:
        raise DelegationError("parent binding session identity does not match")
    if data.get("runtime") != context.runtime:
        raise DelegationError("parent binding runtime does not match")

    binding_workdir = data.get("workdir")
    if not isinstance(binding_workdir, str):
        raise DelegationError("parent binding has invalid workdir")
    resolved_workdir = Path(binding_workdir).expanduser().resolve()
    if resolved_workdir != Path(context.workdir).expanduser().resolve():
        raise DelegationError("parent binding workdir does not match")
    if not resolved_workdir.is_dir():
        raise DelegationError(f"parent workdir does not exist: {resolved_workdir}")

    if data.get("session_kind") != "user" or data.get("delegation_depth") != 0:
        raise DelegationError("only top-level user sessions may delegate")
    if data.get("agent_mcp_enabled") is not True:
        raise DelegationError("parent binding does not enable agent MCP")

    if context.runtime == "codex":
        permission_ok = (
            data.get("effective_sandbox") == "danger-full-access"
            and data.get("effective_approval") == "never"
            and data.get("network_access") is True
        )
    else:
        permission_ok = data.get("permission") == "bypassPermissions"
    if not permission_ok:
        raise DelegationError(
            "effective parent permission does not allow full-access delegation"
        )

    return replace(
        context,
        workdir=str(resolved_workdir),
        permission=(
            "danger-full-access"
            if context.runtime == "codex"
            else "bypassPermissions"
        ),
        memory_enabled=_binding_bool(data, "memory_enabled"),
        profile_enabled=_binding_bool(data, "profile_enabled"),
        self_enabled=_binding_bool(data, "self_enabled"),
        delegation_depth=0,
    )


def _event_error(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        if payload.get("error"):
            return str(payload["error"])
    return f"delegated session ended with {event.get('type', 'unknown')}"


async def _interrupt_session(client: httpx.AsyncClient, session_id: str) -> None:
    response = await client.post(f"/api/agent/sessions/{session_id}/interrupt")
    response.raise_for_status()


async def _delete_session(client: httpx.AsyncClient, session_id: str) -> None:
    response = await client.delete(f"/api/agent/sessions/{session_id}")
    response.raise_for_status()


def _cleanup_timeout_seconds() -> float:
    value = float(os.environ.get("TROWEL_DELEGATE_CLEANUP_TIMEOUT", "10"))
    if value <= 0:
        raise ValueError("TROWEL_DELEGATE_CLEANUP_TIMEOUT must be positive")
    return value


async def _finish_cleanup(operation: Callable[[], Awaitable[T]]) -> T:
    timeout = _cleanup_timeout_seconds()
    cleanup = asyncio.create_task(asyncio.wait_for(operation(), timeout=timeout))
    while True:
        try:
            return await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            if cleanup.done():
                return cleanup.result()
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()


async def _read_child_binding(
    client: httpx.AsyncClient, session_id: str, fallback: dict[str, Any]
) -> dict[str, Any]:
    try:
        response = await client.get(f"/api/agent/sessions/{session_id}")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return dict(data) if isinstance(data, dict) else fallback
    except Exception:
        logger.warning("failed to read final child binding %s", session_id, exc_info=True)
        return fallback


async def delegate_agent(
    client: httpx.AsyncClient,
    *,
    context: ParentContext,
    runtime: str,
    task: str,
    model: str | None = None,
    effort: str | None = None,
) -> DelegationResult:
    if not task.strip():
        raise ValueError("task must not be empty")
    delegation_id = uuid.uuid4().hex
    session_id = ""
    terminal_event = ""
    answer_chunks: list[str] = []
    event_counts: Counter[str] = Counter()
    child_binding: dict[str, Any] = {}
    delete_allowed = True
    primary_error: BaseException | None = None
    result: DelegationResult | None = None
    try:
        verified_context = await _verified_parent_context(client, context)
        create_response = await client.post(
            "/api/agent/sessions",
            json=_create_body(
                verified_context,
                runtime=runtime,
                model=model,
                effort=effort,
            ),
        )
        create_response.raise_for_status()
        create_payload = create_response.json()
        data = create_payload.get("data") if isinstance(create_payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("session_id"), str):
            raise DelegationError("create session response has no data.session_id")
        child_binding = dict(data)
        session_id = data["session_id"]

        async with client.stream(
            "POST",
            f"/api/agent/sessions/{session_id}/messages",
            json={"text": task},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line.removeprefix("data:").strip()
                if not raw:
                    continue
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise DelegationError("SSE data is not an event object")
                event_type = str(event.get("type", ""))
                event_counts[event_type] += 1
                payload = event.get("payload")
                if event_type == "text" and isinstance(payload, dict):
                    text = payload.get("text")
                    if isinstance(text, str):
                        answer_chunks.append(text)
                if event_type in _ALL_TERMINALS:
                    terminal_event = event_type
                    if event_type in _ERROR_TERMINALS:
                        raise DelegationError(_event_error(event))
                    break

        if not terminal_event:
            raise DelegationError("delegated stream ended without terminal event")
        child_binding = await _read_child_binding(client, session_id, child_binding)
        result = DelegationResult(
            delegation_id=delegation_id,
            parent_session_id=verified_context.session_id,
            child_session_id=session_id,
            runtime=runtime,
            answer="".join(answer_chunks),
            terminal_event=terminal_event,
            event_counts=dict(event_counts),
            child_binding=child_binding,
        )
    except BaseException as exc:
        primary_error = exc
        if session_id and not terminal_event:
            try:
                await _finish_cleanup(lambda: _interrupt_session(client, session_id))
            except Exception as interrupt_error:
                delete_allowed = False
                raise DelegationCleanupError(
                    f"interrupt failed; binding {session_id} was preserved"
                ) from interrupt_error
        raise
    finally:
        if session_id and delete_allowed:
            try:
                await _finish_cleanup(lambda: _delete_session(client, session_id))
            except Exception as delete_error:
                if primary_error is None:
                    assert result is not None
                    result = replace(
                        result,
                        cleanup_status="preserved",
                        cleanup_error=(
                            f"delete {session_id} failed after successful turn: "
                            f"{delete_error!r}"
                        ),
                    )
                else:
                    primary_error.add_note(
                        f"delete {session_id} failed after delegation error: "
                        f"{delete_error!r}"
                    )
                logger.exception("delete failed; binding may remain", exc_info=delete_error)
    assert result is not None
    return result


def _tool() -> types.Tool:
    return types.Tool(
        name=_TOOL_DELEGATE,
        description=(
            "Delegate one bounded task to a Trowel-hosted Claude Code or Codex "
            "session. Workdir, permissions and parent identity are inherited "
            "from the current Trowel session and cannot be supplied by the model. "
            "Use delegate_start instead when a Claude task may need guidance."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "target_runtime": {
                    "type": "string",
                    "enum": ["claude_code", "codex"],
                },
                "task": {"type": "string", "minLength": 1},
                "model": {"type": "string"},
                "effort": {"type": "string"},
            },
            "required": ["target_runtime", "task"],
            "additionalProperties": False,
        },
    )


def _interactive_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=_TOOL_DELEGATE_START,
            description=(
                "Start a Claude delegation in the background and immediately return "
                "its delegation_id. Poll delegate_status for guidance or completion; "
                "the child remains live until delegate_close."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_runtime": {
                        "type": "string",
                        "enum": ["claude_code"],
                    },
                    "task": {"type": "string", "minLength": 1},
                    "model": {"type": "string"},
                    "effort": {"type": "string"},
                },
                "required": ["target_runtime", "task"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name=_TOOL_DELEGATE_RESPOND,
            description=(
                "Answer a pending Claude AskUserQuestion and immediately return once "
                "the answer is accepted. Poll delegate_status while the same child "
                "continues."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "delegation_id": {"type": "string", "minLength": 1},
                    "answers": {
                        "type": "object",
                        "description": (
                            "Map every pending question's full question text or unique "
                            "header to its selected answer."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["delegation_id", "answers"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name=_TOOL_DELEGATE_STATUS,
            description=(
                "Read a live interactive delegation from this MCP process. Optionally "
                "wait up to 240 seconds for a version change to avoid rapid polling."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "delegation_id": {"type": "string", "minLength": 1},
                    "after_version": {"type": "integer", "minimum": 0},
                    "wait_seconds": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 240,
                    },
                },
                "required": ["delegation_id"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name=_TOOL_DELEGATE_CLOSE,
            description=(
                "Interrupt if necessary, delete the child session, and close the "
                "interactive delegation handle."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "delegation_id": {"type": "string", "minLength": 1}
                },
                "required": ["delegation_id"],
                "additionalProperties": False,
            },
        ),
    ]


def _tools() -> list[types.Tool]:
    tools = [_tool(), *_interactive_tools()]
    assert tuple(tool.name for tool in tools) == AGENT_MCP_TOOL_NAMES
    return tools


def _text(payload: dict[str, Any]) -> list[types.TextContent]:
    return [
        types.TextContent(
            type="text", text=json.dumps(payload, ensure_ascii=False)
        )
    ]


def _interactive_parent(
    broker: InteractiveBroker,
    delegation_id: str,
) -> ParentContext:
    context = _parent_context()
    if broker.parent_session_id(delegation_id) != context.session_id:
        raise DelegationError("interactive delegation does not belong to this parent")
    return context


def _build_server(broker: InteractiveBroker) -> Server:
    server = Server(_SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return _tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name == _TOOL_DELEGATE:
            async with httpx.AsyncClient(
                base_url=_server_base_url(), timeout=httpx.Timeout(None)
            ) as client:
                result = await delegate_agent(
                    client,
                    context=_parent_context(),
                    runtime=str(arguments.get("target_runtime", "")),
                    task=str(arguments.get("task", "")),
                    model=(
                        str(arguments["model"]) if arguments.get("model") else None
                    ),
                    effort=(
                        str(arguments["effort"])
                        if arguments.get("effort")
                        else None
                    ),
                )
            return _text(result.to_dict())

        delegation_id = str(arguments.get("delegation_id", ""))
        if name == _TOOL_DELEGATE_START:
            runtime = str(arguments.get("target_runtime", ""))
            if runtime != "claude_code":
                raise ValueError("interactive delegation only supports claude_code")
            context = _parent_context()
            async with httpx.AsyncClient(
                base_url=_server_base_url(), timeout=httpx.Timeout(None)
            ) as client:
                context = await _verified_parent_context(client, context)
            return _text(
                await broker.start(
                    parent_session_id=context.session_id,
                    task=str(arguments.get("task", "")),
                    create_body=_create_body(
                        context,
                        runtime=runtime,
                        model=(
                            str(arguments["model"])
                            if arguments.get("model")
                            else None
                        ),
                        effort=(
                            str(arguments["effort"])
                            if arguments.get("effort")
                            else None
                        ),
                    ),
                )
            )
        if name == _TOOL_DELEGATE_RESPOND:
            context = _interactive_parent(broker, delegation_id)
            async with httpx.AsyncClient(
                base_url=_server_base_url(), timeout=httpx.Timeout(None)
            ) as client:
                await _verified_parent_context(client, context)
            raw_answers = arguments.get("answers")
            if not isinstance(raw_answers, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in raw_answers.items()
            ):
                raise ValueError("answers must be an object of strings")
            return _text(await broker.respond(delegation_id, raw_answers))
        if name == _TOOL_DELEGATE_STATUS:
            _interactive_parent(broker, delegation_id)
            raw_after_version = arguments.get("after_version")
            if raw_after_version is not None and (
                isinstance(raw_after_version, bool)
                or not isinstance(raw_after_version, int)
            ):
                raise ValueError("after_version must be an integer")
            raw_wait_seconds = arguments.get("wait_seconds", 0)
            if isinstance(raw_wait_seconds, bool) or not isinstance(
                raw_wait_seconds, (int, float)
            ):
                raise ValueError("wait_seconds must be a number")
            return _text(
                await broker.wait_status(
                    delegation_id,
                    after_version=raw_after_version,
                    wait_seconds=float(raw_wait_seconds),
                )
            )
        if name == _TOOL_DELEGATE_CLOSE:
            _interactive_parent(broker, delegation_id)
            return _text(await broker.close(delegation_id))
        raise ValueError(f"unknown tool: {name}")

    return server


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    broker = InteractiveBroker(
        base_url=_server_base_url(), cleanup_timeout=_cleanup_timeout_seconds()
    )
    server = _build_server(broker)
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(), experimental_capabilities={}
    )
    async with stdio_server() as (read_stream, write_stream):
        try:
            await server.run(read_stream, write_stream, init_options)
        finally:
            await broker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
