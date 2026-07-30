"""Codex session 的冻结配置与原生 thread 事实。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from trowel_py.agent_mcp.launch import (
    AGENT_MCP_SERVER_NAME,
    build_agent_mcp_launch_spec,
)
from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME

TROWEL_AGENTS_SERVER_NAME = AGENT_MCP_SERVER_NAME


@dataclass(frozen=True)
class TrowelMemoryMcpConfig:
    """附加到 Codex thread 的 Trowel memory MCP 配置。

    fresh thread 尚无原生 thread id，native_session_id 必须为空，不能用
    Trowel 会话 id 冒充；resume 时才写入绑定中的真实 thread id。

    Attributes:
        server_name: 写入 ``mcp_servers`` 的服务名称。
        command: 启动 memory MCP server 的解释器或可执行文件。
        module_args: 传给 ``command`` 的固定启动参数。
        memory_root: MCP server 读取的本地 memory 根目录。
        trowel_session_id: 归属该 MCP server 的 Trowel 会话 ID。
    """

    server_name: str
    command: str
    module_args: tuple[str, ...]
    memory_root: str
    trowel_session_id: str

    def to_thread_config(self, *, native_session_id: str = "") -> dict[str, Any]:
        """构造必需且预先授权本地 memory 工具的 MCP server 配置。

        Args:
            native_session_id: resume 时绑定的 Codex thread ID；fresh thread 传空串。

        Returns:
            可合并到 ``config.mcp_servers`` 的单服务映射。
        """

        return {
            self.server_name: {
                "command": self.command,
                "args": list(self.module_args),
                "env": {
                    "MEMORY_ROOT": self.memory_root,
                    "TROWEL_SESSION_ID": self.trowel_session_id,
                    "TROWEL_HOST_KIND": "codex",
                    "TROWEL_NATIVE_SESSION_ID": native_session_id,
                },
                "required": True,
                "enabled_tools": ["search", "read", "outcome"],
                "default_tools_approval_mode": "approve",
            }
        }


@dataclass(frozen=True)
class TrowelAgentMcpConfig:
    """附加到 Codex thread 的 Trowel agent delegation MCP 配置。

    Attributes:
        trowel_session_id: 发起委派的父 Trowel 会话 ID。
        workdir: 子任务继承的父会话工作目录。
        permission: 子任务继承的权限 preset。
        base_url: agent MCP 回调 Trowel Agent Host 的 HTTP 地址。
        memory_enabled: 子任务是否继承 memory 开关。
        profile_enabled: 子任务是否继承 profile 开关。
        self_enabled: 子任务是否继承 self injection 开关。
        delegation_depth: 当前父会话的委派深度；默认 ``0`` 表示顶层会话。
        server_name: 写入 ``mcp_servers`` 的服务名称；默认为 ``trowel_agents``。
    """

    trowel_session_id: str
    workdir: str
    permission: str
    base_url: str
    memory_enabled: bool
    profile_enabled: bool
    self_enabled: bool
    delegation_depth: int = 0
    server_name: str = TROWEL_AGENTS_SERVER_NAME

    def to_thread_config(self, *, native_session_id: str = "") -> dict[str, Any]:
        """构造必需且预先授权委派工具的 Agent MCP server 配置。

        Args:
            native_session_id: resume 时绑定的 Codex thread ID；fresh thread 传空串。

        Returns:
            可合并到 ``config.mcp_servers`` 的单服务映射。
        """

        launch = build_agent_mcp_launch_spec(
            trowel_session_id=self.trowel_session_id,
            runtime="codex",
            workdir=self.workdir,
            permission=self.permission,
            base_url=self.base_url,
            memory_enabled=self.memory_enabled,
            profile_enabled=self.profile_enabled,
            self_enabled=self.self_enabled,
            delegation_depth=self.delegation_depth,
            native_session_id=native_session_id,
        )
        return {
            self.server_name: {
                "command": launch.command,
                "args": list(launch.module_args),
                "env": dict(launch.env),
                "required": True,
                "startup_timeout_sec": 10.0,
                "enabled_tools": list(launch.enabled_tools),
                "default_tools_approval_mode": "approve",
            }
        }


def build_default_trowel_agent_mcp(
    *,
    trowel_session_id: str,
    workdir: str,
    permission: str,
    base_url: str,
    memory_enabled: bool,
    profile_enabled: bool,
    self_enabled: bool,
    delegation_depth: int = 0,
) -> TrowelAgentMcpConfig:
    """用父会话上下文构造默认的 Agent 委派 MCP 配置。

    Args:
        trowel_session_id: 父 Trowel 会话 ID。
        workdir: 子任务继承的工作目录。
        permission: 子任务继承的权限 preset。
        base_url: Agent Host 的 HTTP 地址。
        memory_enabled: 子任务是否继承 memory 开关。
        profile_enabled: 子任务是否继承 profile 开关。
        self_enabled: 子任务是否继承 self injection 开关。
        delegation_depth: 父会话当前的委派深度；默认 ``0``。
    """

    return TrowelAgentMcpConfig(
        trowel_session_id=trowel_session_id,
        workdir=workdir,
        permission=permission,
        base_url=base_url,
        memory_enabled=memory_enabled,
        profile_enabled=profile_enabled,
        self_enabled=self_enabled,
        delegation_depth=delegation_depth,
    )


def build_default_trowel_memory_mcp(
    *,
    trowel_session_id: str,
    memory_root: str,
    server_name: str = TROWEL_NOTE_SEARCH_SERVER_NAME,
) -> TrowelMemoryMcpConfig:
    """用当前解释器构造 Trowel memory MCP 配置。

    Args:
        trowel_session_id: MCP 服务归属的 Trowel 会话 ID。
        memory_root: 本地 memory 根目录。
        server_name: 写入 ``mcp_servers`` 的服务名称；默认使用
            ``TROWEL_NOTE_SEARCH_SERVER_NAME``。
    """

    return TrowelMemoryMcpConfig(
        server_name=server_name,
        command=sys.executable,
        module_args=("-m", "trowel_py.memory.mcp_server"),
        memory_root=str(memory_root),
        trowel_session_id=trowel_session_id,
    )


@dataclass(frozen=True)
class CodexSessionConfig:
    """定义 Codex 会话的冻结输入。

    developer_instructions 会覆盖用户 Codex 配置中的同名值，并非追加；
    ephemeral=False 保留可供 app-server 重启后恢复的 native rollout；
    memory MCP 为 None 时，本会话不附加 Trowel memory MCP。
    """

    trowel_session_id: str
    workdir: str
    model: str | None = None
    effort: str | None = None
    developer_instructions: str | None = None
    approval_policy: str | None = None
    sandbox: str | None = None
    ephemeral: bool = False
    initial_thread_id: str | None = None
    trowel_memory_mcp: TrowelMemoryMcpConfig | None = None
    trowel_agent_mcp: TrowelAgentMcpConfig | None = None


@dataclass(frozen=True)
class ThreadBinding:
    """保存当前 Codex thread 的原生身份和生效配置。

    Attributes:
        thread_id: app-server 分配的原生 thread ID。
        model: thread 响应中的 model 值转换成的字符串。
        model_provider: thread 响应中的 ``modelProvider`` 值转换成的字符串。
        cwd: thread 响应中的 ``cwd`` 值转换成的字符串。
        sandbox: sandbox 为映射时保存其只读浅拷贝，否则为空映射。
        approval_policy: 原始字符串或映射的只读浅拷贝；其他类型转为 ``None``。
        permission_profile: 原生 permission profile 的字符串 ID；没有时为 ``None``。
        effective_sandbox: 归一化的 sandbox 模式；没有可用模式时为 ``None``。
        effective_approval: 从原始策略提取的名称；没有可用名称时为 ``None``。
        network_access: 生效的网络权限；未提供可识别值且无法推断时为 ``None``。
        service_tier: 服务端确认的 service tier；未提供时为 ``None``。
        reasoning_effort: 服务端确认的推理强度；未提供时为 ``None``。
    """

    thread_id: str
    model: str
    model_provider: str
    cwd: str
    sandbox: Mapping[str, Any]
    approval_policy: str | Mapping[str, Any] | None
    permission_profile: str | None = None
    effective_sandbox: str | None = None
    effective_approval: str | None = None
    network_access: bool | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None


def _wire_mode(value: object) -> str | None:
    """将已知 wire 模式改为 kebab-case，并保留未知非空字符串。

    非字符串或空字符串返回 ``None``。
    """

    if not isinstance(value, str) or not value:
        return None
    known = {
        "readOnly": "read-only",
        "workspaceWrite": "workspace-write",
        "dangerFullAccess": "danger-full-access",
        "externalSandbox": "external-sandbox",
    }
    return known.get(value, value)


def _sandbox_facts(value: object) -> tuple[str | None, bool | None]:
    """提取用于展示的 sandbox 模式与网络权限。

    非映射返回两个 ``None``；``danger-full-access`` 未提供可识别的
    ``networkAccess`` 值时仍推断为允许网络。
    """

    if not isinstance(value, Mapping):
        return None, None
    mode = _wire_mode(value.get("type") or value.get("mode"))
    raw_network = value.get("networkAccess")
    if isinstance(raw_network, bool):
        network = raw_network
    elif raw_network == "enabled":
        network = True
    elif raw_network == "restricted":
        network = False
    elif mode == "danger-full-access":
        # dangerFullAccess 即使省略 networkAccess 也明确允许网络。
        network = True
    else:
        network = None
    return mode, network


def _approval_fact(value: object) -> str | None:
    """提取字符串策略或策略映射中的 ``policy`` 名称。"""

    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("policy"), str):
        return str(value["policy"])
    return None


def _permission_profile_fact(value: object) -> str | None:
    """提取原生 permission profile 映射中的字符串 ``id``。"""

    if isinstance(value, Mapping) and isinstance(value.get("id"), str):
        return str(value["id"])
    return None


def parse_thread_binding(result: Mapping[str, Any]) -> ThreadBinding:
    """解析原生 thread 响应中的身份、配置和展示事实。

    原始策略映射使用只读代理保存，公开字段另行归一化。

    Args:
        result: app-server 返回的 thread 结果映射。

    Returns:
        包含原始只读策略和归一化展示字段的 thread 绑定。

    Raises:
        ProtocolViolationError: 缺少非空 ``thread.id``，或缺少 ``model``、
            ``modelProvider``、``cwd`` 任一顶层字段。
    """

    thread = result.get("thread")
    if not isinstance(thread, Mapping) or not thread.get("id"):
        raise ProtocolViolationError(
            "thread/start response has no thread.id",
            payload=dict(result),
        )
    for required in ("model", "modelProvider", "cwd"):
        if required not in result:
            raise ProtocolViolationError(
                f"thread response missing effective fact {required!r}",
                payload=dict(result),
            )
    sandbox = result.get("sandbox")
    approval_policy = result.get("approvalPolicy")
    effective_sandbox, network_access = _sandbox_facts(sandbox)
    effective_approval = _approval_fact(approval_policy)
    permission_profile = _permission_profile_fact(result.get("activePermissionProfile"))
    return ThreadBinding(
        thread_id=str(thread["id"]),
        model=str(result["model"]),
        model_provider=str(result["modelProvider"]),
        cwd=str(result["cwd"]),
        sandbox=MappingProxyType(dict(sandbox))
        if isinstance(sandbox, Mapping)
        else MappingProxyType({}),
        approval_policy=(
            MappingProxyType(dict(approval_policy))
            if isinstance(approval_policy, Mapping)
            else str(approval_policy)
            if isinstance(approval_policy, str)
            else None
        ),
        permission_profile=permission_profile,
        effective_sandbox=effective_sandbox,
        effective_approval=effective_approval,
        network_access=network_access,
        service_tier=str(result["serviceTier"])
        if result.get("serviceTier") is not None
        else None,
        reasoning_effort=str(result["reasoningEffort"])
        if result.get("reasoningEffort") is not None
        else None,
    )


# 这些对象长期从 session.py 导入；保留原 FQN 以兼容 pickle 与类型诊断。
_PUBLIC_SESSION_MODULE = "trowel_py.codex_host.session"
for _public_symbol in (
    TrowelMemoryMcpConfig,
    TrowelAgentMcpConfig,
    CodexSessionConfig,
    ThreadBinding,
    build_default_trowel_agent_mcp,
    build_default_trowel_memory_mcp,
    parse_thread_binding,
):
    _public_symbol.__module__ = _PUBLIC_SESSION_MODULE
del _public_symbol
