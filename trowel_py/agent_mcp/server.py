"""通过 stdio MCP 提供阻塞委派和可交互委派工具。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

import httpx
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from trowel_py.agent_mcp import AGENT_MCP_TOOL_NAMES
from trowel_py.agent_mcp.http_errors import agent_api_error_detail
from trowel_py.agent_mcp.interactive_client import InteractiveBrokerClient
from trowel_py.agent_mcp.launch import AGENT_MCP_SERVER_NAME

_SERVER_NAME = AGENT_MCP_SERVER_NAME
_TOOL_DELEGATE = "delegate"
_TOOL_DELEGATE_START = "delegate_start"
_TOOL_DELEGATE_RESPOND = "delegate_respond"
_TOOL_DELEGATE_STATUS = "delegate_status"
_TOOL_DELEGATE_CLOSE = "delegate_close"
_CLAUDE_ALWAYS_LOAD_META = {"anthropic/alwaysLoad": True}
_TOOL_DISCOVERY_TIMEOUT_SECONDS = 3.0
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
        delegation_targets: 父会话创建时冻结的稳定别名与启动事实。
    """

    session_id: str
    runtime: str
    workdir: str
    permission: str
    memory_enabled: bool
    profile_enabled: bool
    self_enabled: bool
    delegation_depth: int
    delegation_targets: tuple[DelegationTargetContext, ...] = ()


@dataclass(frozen=True)
class DelegationTargetContext:
    """保存 MCP 进程从父 binding 读取的一项冻结调用目标。

    Attributes:
        alias: 父模型使用的稳定调用别名。
        runtime: 子会话使用 Claude Code 还是 Codex。
        connection_id: Trowel 设置域模型连接 ID。
        connection_identity_version: 父会话冻结的连接启动身份版本。
        model: 子会话模型或 Claude 角色别名。
        effort: 子会话思考强度；None 表示 runtime 默认值。
    """

    alias: str
    runtime: str
    connection_id: str
    connection_identity_version: int
    model: str
    effort: str | None


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


def _agent_host_headers() -> dict[str, str] | None:
    """读取桌面模式下 Agent MCP 访问 Agent Host 所需的实例凭据。

    新启动配置使用职责单一的 API 凭据变量。资源登记凭据仅作为旧 Codex
    配置的兼容回退，避免恢复中的历史会话失去 Agent Host 访问能力。
    """

    credential = os.environ.get("TROWEL_AGENT_API_CREDENTIAL", "").strip()
    if not credential:
        credential = os.environ.get(
            "TROWEL_RESOURCE_REGISTRATION_CREDENTIAL", ""
        ).strip()
    return {"Authorization": f"Bearer {credential}"} if credential else None


def _agent_host_client() -> httpx.AsyncClient:
    """创建带完整桌面认证的 Agent Host 客户端。

    所有委派操作都必须经同一入口访问 Agent Host，避免阻塞委派、交互委派和
    broker 各自拼装客户端时漏掉实例凭据。

    Returns:
        关闭超时并携带当前 sidecar Bearer 的异步 HTTP 客户端。
    """

    return httpx.AsyncClient(
        base_url=_server_base_url(),
        headers=_agent_host_headers(),
        timeout=httpx.Timeout(None),
    )


def _create_body(
    context: ParentContext,
    *,
    target: DelegationTargetContext,
) -> dict[str, Any]:
    """构建不会递归委派、不会进入自动记忆提炼的子会话请求。

    Args:
        context: 已复核的父会话事实和功能开关。
        target: 父会话创建时冻结的别名解析结果。

    Returns:
        可发送给 Agent API 的子会话创建请求体。
    """

    runtime = target.runtime
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
        "delegation_configuration": target.alias,
        "connection_id": target.connection_id,
        "expected_connection_identity_version": (target.connection_identity_version),
        "model": target.model,
    }
    if runtime == "codex":
        body["permission_preset"] = "danger-full-access"
    else:
        body["permission_mode"] = "bypassPermissions"
    if target.effort:
        body["effort"] = target.effort
    return body


def _delegation_target(
    context: ParentContext,
    configuration: str,
    *,
    interactive: bool = False,
) -> DelegationTargetContext:
    """按父会话冻结清单解析稳定别名，并执行交互 capability 门禁。"""

    alias = configuration.strip()
    target = next(
        (item for item in context.delegation_targets if item.alias == alias),
        None,
    )
    if target is None:
        available = ", ".join(repr(item.alias) for item in context.delegation_targets)
        raise DelegationError(
            f"configuration {alias!r} is unavailable in this parent session; "
            f"available configurations: {available or 'none'}"
        )
    if interactive and target.runtime != "claude_code":
        raise DelegationError(
            "interactive delegation currently supports Claude Code configurations only"
        )
    return target


def _binding_delegation_targets(
    data: dict[str, Any],
) -> tuple[DelegationTargetContext, ...]:
    """从父 binding 解析完整有效的冻结调用目标，损坏项保守丢弃。"""

    raw_targets = data.get("delegation_targets")
    if not isinstance(raw_targets, list):
        return ()
    targets: list[DelegationTargetContext] = []
    for raw in raw_targets:
        if not isinstance(raw, dict):
            continue
        try:
            alias = raw["alias"]
            runtime = raw["runtime"]
            connection_id = raw["connection_id"]
            connection_identity_version = raw["connection_identity_version"]
            model = raw["model"]
            effort = raw.get("effort")
            if (
                not all(
                    isinstance(item, str) and item
                    for item in (alias, runtime, connection_id, model)
                )
                or runtime not in {"claude_code", "codex"}
                or not isinstance(connection_identity_version, int)
                or isinstance(connection_identity_version, bool)
                or connection_identity_version < 1
                or (effort is not None and not isinstance(effort, str))
            ):
                continue
            targets.append(
                DelegationTargetContext(
                    alias=alias,
                    runtime=runtime,
                    connection_id=connection_id,
                    connection_identity_version=connection_identity_version,
                    model=model,
                    effort=effort,
                )
            )
        except KeyError:
            continue
    return tuple(targets)


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
            "danger-full-access" if context.runtime == "codex" else "bypassPermissions"
        ),
        memory_enabled=_binding_bool(data, "memory_enabled"),
        profile_enabled=_binding_bool(data, "profile_enabled"),
        self_enabled=_binding_bool(data, "self_enabled"),
        delegation_depth=0,
        delegation_targets=_binding_delegation_targets(data),
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
        logger.warning(
            "failed to read final child binding %s", session_id, exc_info=True
        )
        return fallback


async def delegate_agent(
    client: httpx.AsyncClient,
    *,
    context: ParentContext,
    task: str,
    configuration: str,
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
        configuration: 父会话创建时冻结的稳定调用别名。
        task: 交给子会话执行的任务。

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
        target = _delegation_target(verified_context, configuration)
        create_body = _create_body(verified_context, target=target)
        create_response = await client.post(
            "/api/agent/sessions",
            json=create_body,
        )
        create_error = agent_api_error_detail(create_response)
        if create_error is not None:
            raise DelegationError(create_error)
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
            runtime=target.runtime,
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
                logger.exception(
                    "delete failed; binding may remain", exc_info=delete_error
                )
    assert result is not None
    return result


def _configuration_property(
    targets: Sequence[DelegationTargetContext],
) -> dict[str, Any]:
    """把父会话冻结别名转换成模型可直接选择的 JSON Schema。

    Args:
        targets: 当前工具允许使用的父会话冻结调用目标。

    Returns:
        有目标时带有动态 ``enum``；启动期无法读取父 binding 时保留非空字符串
        兼容 schema，避免 Agent MCP 整体不可用。
    """

    aliases = list(dict.fromkeys(target.alias for target in targets))
    if aliases:
        return {"type": "string", "enum": aliases}
    return {"type": "string", "minLength": 1}


def _configuration_note(
    targets: Sequence[DelegationTargetContext],
) -> str:
    """生成人类和模型都能直接读取的动态别名说明。"""

    if not targets:
        return ""
    aliases = ", ".join(f"`{target.alias}`" for target in targets)
    return f" Available configurations for this parent session: {aliases}."


def _tool(
    targets: Sequence[DelegationTargetContext] = (),
) -> types.Tool:
    """定义一次性阻塞委派工具及其动态输入格式。

    Args:
        targets: 父会话创建时冻结的全部可调用配置。
    """

    return types.Tool(
        name=_TOOL_DELEGATE,
        _meta=_CLAUDE_ALWAYS_LOAD_META,
        description=(
            "Delegate one bounded task through a named Trowel runtime configuration. "
            "Runtime, connection, model, effort, workdir and permissions are frozen "
            "by the parent session and cannot be supplied by the model. "
            "Use delegate_start instead when a Claude task may need guidance or "
            "run for several minutes."
            f"{_configuration_note(targets)}"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "configuration": _configuration_property(targets),
                "task": {"type": "string", "minLength": 1},
            },
            "required": ["configuration", "task"],
            "additionalProperties": False,
        },
    )


def _interactive_tools(
    targets: Sequence[DelegationTargetContext] = (),
) -> list[types.Tool]:
    """定义启动、回答、查询和关闭交互委派的四个工具。

    Args:
        targets: 父会话冻结目标；交互入口只展示已验证的 Claude 配置。
    """

    interactive_targets = tuple(
        target for target in targets if target.runtime == "claude_code"
    )

    return [
        types.Tool(
            name=_TOOL_DELEGATE_START,
            _meta=_CLAUDE_ALWAYS_LOAD_META,
            description=(
                "Start a long-running or interactive Claude delegation in the "
                "background and immediately return its handle. Continue independent "
                "parent work without polling. When the child asks or finishes, Agent "
                "Host automatically starts a parent turn after the current turn is "
                "idle. The child remains live until delegate_close."
                f"{_configuration_note(interactive_targets)}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "configuration": _configuration_property(interactive_targets),
                    "task": {"type": "string", "minLength": 1},
                },
                "required": ["configuration", "task"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name=_TOOL_DELEGATE_RESPOND,
            _meta=_CLAUDE_ALWAYS_LOAD_META,
            description=(
                "Answer a pending Claude AskUserQuestion and immediately return "
                "after the answer is accepted. Continue independent parent work; "
                "Agent Host automatically delivers the next question or terminal "
                "state when the parent session is idle."
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
            _meta=_CLAUDE_ALWAYS_LOAD_META,
            description=(
                "Immediately read a live interactive delegation. Do not repeatedly "
                "poll while the child is running. Normal actionable updates are "
                "delivered automatically; use this tool only for recovery or an "
                "explicit status check."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "delegation_id": {"type": "string", "minLength": 1},
                },
                "required": ["delegation_id"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name=_TOOL_DELEGATE_CLOSE,
            _meta=_CLAUDE_ALWAYS_LOAD_META,
            description=(
                "Interrupt if necessary, delete the child session, and close the "
                "interactive delegation handle."
            ),
            inputSchema={
                "type": "object",
                "properties": {"delegation_id": {"type": "string", "minLength": 1}},
                "required": ["delegation_id"],
                "additionalProperties": False,
            },
        ),
    ]


def _tools(
    targets: Sequence[DelegationTargetContext] = (),
) -> list[types.Tool]:
    """按固定顺序组装并校验 Agent MCP 的全部工具。

    Args:
        targets: 父会话冻结目标；为空时生成启动故障下的兼容契约。
    """

    tools = [_tool(targets), *_interactive_tools(targets)]
    assert tuple(tool.name for tool in tools) == AGENT_MCP_TOOL_NAMES
    return tools


async def _published_tools(client: httpx.AsyncClient) -> list[types.Tool]:
    """从 Agent Host 读取父会话事实并渲染动态工具契约。

    Args:
        client: 已配置实例认证的 Agent Host 客户端。

    Returns:
        ``configuration`` schema 和说明均来自当前父 binding 冻结目标的工具列表。
    """

    context = await _verified_parent_context(client, _parent_context())
    return _tools(context.delegation_targets)


def _text(payload: dict[str, Any]) -> list[types.TextContent]:
    """把字典编码为单个 JSON MCP 文本结果。"""

    return [
        types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))
    ]


def _build_server(broker: InteractiveBrokerClient) -> Server:
    """创建 MCP 服务，并注册工具列表和调用分发。

    Args:
        broker: 访问 Agent Host 应用级委派状态的客户端。

    Returns:
        已注册委派工具处理器的 MCP 服务。
    """

    server = Server(_SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        """返回当前 MCP 服务发布的全部委派工具。"""

        try:
            async with asyncio.timeout(_TOOL_DISCOVERY_TIMEOUT_SECONDS):
                async with _agent_host_client() as client:
                    return await _published_tools(client)
        except Exception:
            logger.warning(
                "failed to publish parent-specific Agent MCP aliases; "
                "falling back to the compatibility schema",
                exc_info=True,
            )
            return _tools()

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        """校验工具参数并分发到阻塞或交互委派实现。

        阻塞委派、交互委派启动和回答操作会通过 Agent API 复核父会话的当前
        绑定；查询和关闭操作把 MCP 环境中的父会话身份交给 Agent Host 复核。

        Args:
            name: MCP 客户端请求调用的工具名称。
            arguments: 工具调用携带的结构化参数。

        Returns:
            编码为 JSON 文本的工具结果。
        """

        if name == _TOOL_DELEGATE:
            async with _agent_host_client() as client:
                result = await delegate_agent(
                    client,
                    context=_parent_context(),
                    configuration=str(arguments.get("configuration", "")),
                    task=str(arguments.get("task", "")),
                )
            return _text(result.to_dict())

        delegation_id = str(arguments.get("delegation_id", ""))
        if name == _TOOL_DELEGATE_START:
            configuration = str(arguments.get("configuration", ""))
            context = _parent_context()
            async with _agent_host_client() as client:
                context = await _verified_parent_context(client, context)
            target = _delegation_target(context, configuration, interactive=True)
            return _text(
                await broker.start(
                    parent_session_id=context.session_id,
                    task=str(arguments.get("task", "")),
                    create_body=_create_body(context, target=target),
                )
            )
        if name == _TOOL_DELEGATE_RESPOND:
            context = _parent_context()
            async with _agent_host_client() as client:
                await _verified_parent_context(client, context)
            raw_answers = arguments.get("answers")
            if not isinstance(raw_answers, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in raw_answers.items()
            ):
                raise ValueError("answers must be an object of strings")
            return _text(
                await broker.respond(
                    delegation_id,
                    raw_answers,
                    parent_session_id=context.session_id,
                )
            )
        if name == _TOOL_DELEGATE_STATUS:
            context = _parent_context()
            return _text(
                await broker.status(
                    delegation_id,
                    parent_session_id=context.session_id,
                )
            )
        if name == _TOOL_DELEGATE_CLOSE:
            context = _parent_context()
            return _text(
                await broker.close(
                    delegation_id,
                    parent_session_id=context.session_id,
                )
            )
        raise ValueError(f"unknown tool: {name}")

    return server


async def main() -> None:
    """运行无状态 stdio MCP 服务；交互委派由 Agent Host 生命周期持有。"""

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    from trowel_py.resource_lifecycle.reporting import report_current_process

    await report_current_process()
    broker = InteractiveBrokerClient(
        base_url=_server_base_url(),
        headers=_agent_host_headers(),
    )
    server = _build_server(broker)
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(), experimental_capabilities={}
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
