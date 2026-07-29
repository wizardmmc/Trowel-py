"""通过 stdio MCP 提供阻塞委派和可交互委派工具。"""

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
    """表示委派请求无效或执行失败。"""

    pass


class DelegationCleanupError(DelegationError):
    """表示无法确认子会话已中断，因此保留其绑定。"""

    pass


@dataclass(frozen=True)
class ParentContext:
    """保存父会话身份，以及委派时必须复核或继承的配置。

    Attributes:
        session_id: 发起委派的 Trowel 会话 ID。
        runtime: 父会话由 Claude Code 还是 Codex 运行，分别记录为
            "claude_code" 或 "codex"。
        workdir: 子会话必须继承的父会话工作目录。
        permission: 环境快照或复核结果中记录的父会话权限模式；Claude Code 为
            "bypassPermissions"，Codex 为 "danger-full-access"。
        memory_enabled: 是否向子会话提供父会话可用的 Memory 内容和读取入口。
        profile_enabled: 是否向子会话提供父会话可用的用户画像。
        self_enabled: 是否向子会话提供父会话可用的 Trowel 持续身份信息。
        delegation_depth: 父会话的委派深度；只有 0 允许继续委派。
    """

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
    """保存一次阻塞委派的子会话结果和清理状态。

    Attributes:
        delegation_id: 本次阻塞委派的 ID。
        parent_session_id: 通过当前绑定复核的父会话 ID。
        child_session_id: Trowel 为子会话分配的 ID。
        runtime: 子会话使用的 runtime。
        answer: 按到达顺序拼接的子会话 text 事件内容。
        terminal_event: 从子会话事件流观察到的成功终态类型。
        event_counts: 按事件类型统计的子会话事件数量。
        child_binding: 创建响应中的子会话绑定；成功结束后若能读取最新绑定，
            则替换为该绑定。
        cleanup_status: 子会话的清理结果，为 "deleted" 或 "preserved"。
        cleanup_error: 删除失败的原因；成功删除时为 None。
    """

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
        """转换为分别记录观测事实、子会话回答和清理结果的工具响应。

        Returns:
            包含父子会话标识、观测事实、子会话回答和清理结果的字典。
        """

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
    """读取布尔环境变量，忽略首尾空白和大小写，只接受 true 或 false。

    Args:
        name: 要读取的环境变量名。
        default: 环境变量不存在时使用的值。

    Returns:
        环境变量表示的布尔值，或变量不存在时的默认值。

    Raises:
        DelegationError: 环境变量存在，但值不是 true 或 false。
    """

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
    """从环境变量读取父会话配置快照，并拒绝无效配置、权限模式不足或递归委派。"""

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
    """读取 Trowel Agent API 地址，并要求使用有效的 HTTP 或 HTTPS URL。"""

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
    """构建不会递归委派、不会进入自动记忆提炼的子会话请求。

    Args:
        context: 已复核的父会话事实和功能开关。
        runtime: 子会话使用的 runtime，为 "claude_code" 或 "codex"。
        model: 请求子会话使用的模型；为 None 时不指定。
        effort: 请求子会话使用的思考强度；为 None 时不指定。

    Returns:
        可发送给 Agent API 的子会话创建请求体。
    """

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
    """从父会话绑定记录中读取一个必须存在的布尔字段。

    Args:
        data: Agent API 返回的父会话绑定信息。
        field: 要读取的字段名。
    """

    value = data.get(field)
    if not isinstance(value, bool):
        raise DelegationError(f"parent binding has invalid {field}")
    return value


async def _verified_parent_context(
    client: httpx.AsyncClient,
    context: ParentContext,
) -> ParentContext:
    """用当前绑定复核环境快照中的父会话身份、工作目录、委派资格和授权条件。

    Args:
        client: 用于读取父会话绑定的 Agent API 客户端。
        context: MCP 进程启动时从环境变量取得的父会话配置快照。

    Returns:
        经当前绑定复核的父会话上下文。身份、runtime 和工作目录必须与环境
        快照一致；Codex 的 sandbox、approval 和网络状态或 Claude Code 的
        权限模式必须继续满足委派要求；功能开关取当前绑定值。
    """

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
    """从子会话错误终态中提取供父会话显示的原因。"""

    payload = event.get("payload")
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        if payload.get("error"):
            return str(payload["error"])
    return f"delegated session ended with {event.get('type', 'unknown')}"


async def _interrupt_session(client: httpx.AsyncClient, session_id: str) -> None:
    """请求 Agent API 中断指定子会话。"""

    response = await client.post(f"/api/agent/sessions/{session_id}/interrupt")
    response.raise_for_status()


async def _delete_session(client: httpx.AsyncClient, session_id: str) -> None:
    """请求 Agent API 移除指定子会话，使 Trowel 不再管理它。"""

    response = await client.delete(f"/api/agent/sessions/{session_id}")
    response.raise_for_status()


def _cleanup_timeout_seconds() -> float:
    """读取委派清理时限的秒数，默认 10 秒且必须为正数。"""

    value = float(os.environ.get("TROWEL_DELEGATE_CLEANUP_TIMEOUT", "10"))
    if value <= 0:
        raise ValueError("TROWEL_DELEGATE_CLEANUP_TIMEOUT must be positive")
    return value


async def _finish_cleanup(operation: Callable[[], Awaitable[T]]) -> T:
    """等待一次清理操作完成，即使外层任务在等待期间被取消。

    本函数不会重新抛出外层任务在等待期间收到的取消异常；清理操作自身的
    异常、取消和超时仍向上传播。

    Args:
        operation: 尚未启动的异步清理操作。

    Returns:
        清理操作的返回值。
    """

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
    """读取子会话结束后的绑定信息，读取失败时保留已有信息。

    Args:
        client: 用于读取子会话的 Agent API 客户端。
        session_id: 子会话的 Trowel 会话 ID。
        fallback: 创建子会话时已经取得的绑定信息。

    Returns:
        最新绑定信息；请求或响应无效时返回 fallback。
    """

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
    """执行一次阻塞委派，等待子会话终态，并在返回或抛错前尝试清理。

    创建子会话前会用 Agent API 复核父会话的当前绑定。调用被取消或委派
    失败且尚未观察到终态时，先中断子会话；中断成功后才允许删除，中断
    失败则保留绑定并抛出 DelegationCleanupError。成功委派后的删除失败
    仍返回回答，并把清理状态标为 "preserved"；委派已经失败时，删除错误
    附加到原异常。

    Args:
        client: 用于复核父会话并管理子会话的 Agent API 客户端。
        context: MCP 进程启动时从环境变量取得的父会话配置快照。
        runtime: 子会话使用的 runtime，为 "claude_code" 或 "codex"。
        task: 交给子会话执行的任务。
        model: 请求子会话使用的模型；为 None 时不指定。
        effort: 请求子会话使用的思考强度；为 None 时不指定。

    Returns:
        子会话的成功回答、观察事实、最终绑定和清理结果。

    Raises:
        ValueError: 任务为空或子会话 runtime 不受支持。
        DelegationError: 父会话无权委派、子会话响应无效或返回失败终态。
        DelegationCleanupError: 中断失败，无法确认子会话可以安全移除。
    """

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
    """定义一次性阻塞委派工具及其输入格式。"""

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
    """定义启动、回答、查询和关闭交互委派的四个工具。"""

    return [
        types.Tool(
            name=_TOOL_DELEGATE_START,
            description=(
                "Start a Claude delegation and return when it asks for guidance "
                "or reaches a terminal state. The child remains live until "
                "delegate_close."
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
                "Answer a pending Claude AskUserQuestion and wait for the same "
                "child to ask again or finish."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "delegation_id": {"type": "string", "minLength": 1},
                    "answers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["delegation_id", "answers"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name=_TOOL_DELEGATE_STATUS,
            description="Read a live interactive delegation from this MCP process.",
            inputSchema={
                "type": "object",
                "properties": {
                    "delegation_id": {"type": "string", "minLength": 1}
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
    """按固定顺序组装并校验 Agent MCP 的全部工具。"""

    tools = [_tool(), *_interactive_tools()]
    assert tuple(tool.name for tool in tools) == AGENT_MCP_TOOL_NAMES
    return tools


def _text(payload: dict[str, Any]) -> list[types.TextContent]:
    """把字典编码为单个 JSON MCP 文本结果。"""

    return [
        types.TextContent(
            type="text", text=json.dumps(payload, ensure_ascii=False)
        )
    ]


def _interactive_parent(
    broker: InteractiveBroker,
    delegation_id: str,
) -> ParentContext:
    """重新读取并校验 MCP 环境中的父会话配置，再确认交互委派句柄属于该会话。"""

    context = _parent_context()
    if broker.parent_session_id(delegation_id) != context.session_id:
        raise DelegationError("interactive delegation does not belong to this parent")
    return context


def _build_server(broker: InteractiveBroker) -> Server:
    """创建 MCP 服务，并注册工具列表和调用分发。

    Args:
        broker: 持有进程内交互委派状态的 broker。

    Returns:
        已注册委派工具处理器的 MCP 服务。
    """

    server = Server(_SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        """返回当前 MCP 服务发布的全部委派工具。"""

        return _tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        """校验工具参数并分发到阻塞或交互委派实现。

        阻塞委派、交互委派启动和回答操作会通过 Agent API 复核父会话的当前
        绑定；查询和关闭操作只重新校验 MCP 环境中的父会话配置和句柄归属，
        不读取 Agent API 当前绑定。

        Args:
            name: MCP 客户端请求调用的工具名称。
            arguments: 工具调用携带的结构化参数。

        Returns:
            编码为 JSON 文本的工具结果。
        """

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
            return _text(broker.status(delegation_id))
        if name == _TOOL_DELEGATE_CLOSE:
            _interactive_parent(broker, delegation_id)
            return _text(await broker.close(delegation_id))
        raise ValueError(f"unknown tool: {name}")

    return server


async def main() -> None:
    """运行 stdio MCP 服务，并在退出时尝试清理进程仍在管理的交互委派。"""

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
