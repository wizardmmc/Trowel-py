"""集中定义 Agent MCP 子进程的名称、命令、工具和父会话环境。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from trowel_py.agent_mcp import AGENT_MCP_TOOL_NAMES

AGENT_MCP_SERVER_NAME = "trowel_agents"
AGENT_MCP_MODULE_ARGS = ("-m", "trowel_py.agent_mcp.server")


@dataclass(frozen=True)
class AgentMcpLaunchSpec:
    """记录两种 runtime 启动同一个 Agent MCP server 所需的共同事实。

    Attributes:
        server_name: runtime 配置中登记的 MCP server 名称。
        command: 启动 MCP server 使用的 Python 解释器。
        module_args: 交给解释器的模块启动参数。
        env: MCP server 读取的父会话上下文环境变量。
        enabled_tools: Codex 等支持工具白名单的 host 应预先启用的工具。
    """

    server_name: str
    command: str
    module_args: tuple[str, ...]
    env: Mapping[str, str]
    enabled_tools: tuple[str, ...]


def build_agent_mcp_launch_spec(
    *,
    trowel_session_id: str,
    runtime: str,
    workdir: str,
    permission: str,
    base_url: str,
    memory_enabled: bool,
    profile_enabled: bool,
    self_enabled: bool,
    delegation_depth: int,
    native_session_id: str | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> AgentMcpLaunchSpec:
    """用父会话上下文构造不依赖具体 runtime 配置格式的启动规格。

    ``native_session_id=None`` 表示 host 尚不能声明该环境变量；空字符串表示
    runtime 明确知道当前仍未绑定原生会话。

    Args:
        trowel_session_id: 发起委派的父 Trowel 会话 ID。
        runtime: 父会话使用的 runtime 名称。
        workdir: 子任务继承的父会话工作目录。
        permission: 子任务继承的父会话权限设置。
        base_url: Agent MCP 回调 Trowel Agent Host 的 HTTP 地址。
        memory_enabled: 子任务是否继承 memory 开关。
        profile_enabled: 子任务是否继承 profile 开关。
        self_enabled: 子任务是否继承 Self 注入开关。
        delegation_depth: 当前父会话的委派深度。
        native_session_id: 父 runtime 已知的原生会话 ID。
        extra_env: runtime 需要一并交给 Agent MCP 的其他环境变量。

    Returns:
        可由 Claude Code 或 Codex 分别序列化的共同启动规格。
    """

    env = dict(extra_env or {})
    env.update(
        {
            "TROWEL_AGENT_BASE_URL": base_url,
            "TROWEL_PARENT_SESSION_ID": trowel_session_id,
            "TROWEL_PARENT_RUNTIME": runtime,
            "TROWEL_PARENT_WORKDIR": workdir,
            "TROWEL_PARENT_PERMISSION": permission,
            "TROWEL_PARENT_MEMORY_ENABLED": str(memory_enabled).lower(),
            "TROWEL_PARENT_PROFILE_ENABLED": str(profile_enabled).lower(),
            "TROWEL_PARENT_SELF_ENABLED": str(self_enabled).lower(),
            "TROWEL_DELEGATION_DEPTH": str(delegation_depth),
        }
    )
    if native_session_id is not None:
        env["TROWEL_NATIVE_SESSION_ID"] = native_session_id
    return AgentMcpLaunchSpec(
        server_name=AGENT_MCP_SERVER_NAME,
        command=sys.executable,
        module_args=AGENT_MCP_MODULE_ARGS,
        env=MappingProxyType(env),
        enabled_tools=AGENT_MCP_TOOL_NAMES,
    )
