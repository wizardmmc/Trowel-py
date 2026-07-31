"""Codex manager 发往 app-server 的请求参数构造。"""

from __future__ import annotations

from typing import Any

from trowel_py.codex_host.session import CodexSession


def thread_start_params(session: CodexSession) -> dict[str, Any]:
    """构造 ``thread/start`` 参数，并合并已启用的 Trowel MCP 服务。

    工作目录和 ephemeral 标记始终发送；权限、模型、基础指令和开发者指令只在
    会话配置显式提供时发送。
    """

    config = session.config
    params: dict[str, Any] = {
        "cwd": config.workdir,
        "ephemeral": config.ephemeral,
    }
    if config.approval_policy is not None:
        params["approvalPolicy"] = config.approval_policy
    if config.sandbox is not None:
        params["sandbox"] = config.sandbox
    if config.model is not None:
        params["model"] = config.model
    if config.base_instructions is not None:
        params["baseInstructions"] = config.base_instructions
    if config.developer_instructions is not None:
        params["developerInstructions"] = config.developer_instructions
    servers: dict[str, Any] = {}
    if config.trowel_memory_mcp is not None:
        servers.update(config.trowel_memory_mcp.to_thread_config())
    if config.trowel_agent_mcp is not None:
        servers.update(config.trowel_agent_mcp.to_thread_config())
    if servers:
        params["config"] = {"mcp_servers": servers}
    return params


def thread_resume_params(session: CodexSession) -> dict[str, Any]:
    """构造 ``thread/resume`` 参数，重发工作目录并按需重发权限与 MCP 配置。

    会话必须已有 binding。app-server 不持久化 MCP 配置，恢复时各 MCP 服务会使用
    真实 thread ID 重新生成配置；显式重发权限可避免恢复后回退到进程默认值。
    """

    assert session.binding is not None
    config = session.config
    params: dict[str, Any] = {
        "threadId": session.binding.thread_id,
        "cwd": config.workdir,
    }
    if config.approval_policy is not None:
        params["approvalPolicy"] = config.approval_policy
    if config.sandbox is not None:
        params["sandbox"] = config.sandbox
    servers: dict[str, Any] = {}
    if session.config.trowel_memory_mcp is not None:
        servers.update(
            session.config.trowel_memory_mcp.to_thread_config(
                native_session_id=session.binding.thread_id
            )
        )
    if session.config.trowel_agent_mcp is not None:
        servers.update(
            session.config.trowel_agent_mcp.to_thread_config(
                native_session_id=session.binding.thread_id
            )
        )
    if servers:
        params["config"] = {"mcp_servers": servers}
    return params


def turn_start_params(
    thread_id: str,
    text: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    approval: str | None = None,
    sandbox: str | None = None,
) -> dict[str, Any]:
    """构造 ``turn/start`` 文本输入和单轮设置覆盖。

    Args:
        thread_id: 接收新 turn 的 Codex thread ID。
        text: 用户输入文本。
        model: 单轮模型覆盖；None 表示不发送。
        effort: 单轮推理强度覆盖；None 表示不发送。
        approval: 单轮审批策略覆盖；None 表示不发送。
        sandbox: 单轮沙箱预设；已知值转换为 ``sandboxPolicy`` 对象，None 或未知值
            不发送覆盖。

    Returns:
        包含 thread ID 和单个文本输入的参数；非 None 的模型、推理强度和审批策略会
        直接加入，已知沙箱预设会以对象形式加入。
    """

    # Text 输入即使没有富文本片段也必须携带空 text_elements。
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": text, "text_elements": []}],
    }
    if model is not None:
        params["model"] = model
    if effort is not None:
        params["effort"] = effort
    if approval is not None:
        # approvalPolicy 使用 AskForApproval 字符串枚举，不是沙箱对象。
        params["approvalPolicy"] = approval
    sandbox_policy = sandbox_policy_from_preset(sandbox)
    if sandbox_policy is not None:
        # turn/start 使用 SandboxPolicy 对象；thread/start 和 thread/resume 使用字符串。
        params["sandboxPolicy"] = sandbox_policy
    return params


def sandbox_policy_from_preset(preset: str | None) -> dict[str, Any] | None:
    """将已知沙箱预设转换为 ``turn/start`` 的 SandboxPolicy 对象。

    ``workspace-write`` 显式发送空 ``writableRoots``、关闭网络和两个为 false 的临时
    目录排除开关，避免从 ``danger-full-access`` 降权时依赖服务端默认值。``None`` 或
    未知预设返回 None，调用方不会发送沙箱覆盖。
    """

    if preset is None:
        return None
    mapping: dict[str, dict[str, Any]] = {
        "danger-full-access": {"type": "dangerFullAccess"},
        "read-only": {"type": "readOnly", "networkAccess": False},
        "workspace-write": {
            "type": "workspaceWrite",
            "writableRoots": [],
            "networkAccess": False,
            "excludeTmpdirEnvVar": False,
            "excludeSlashTmp": False,
        },
    }
    return mapping.get(preset)
