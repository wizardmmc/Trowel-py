"""Codex manager 发往 app-server 的请求参数构造。"""

from __future__ import annotations

from typing import Any

from trowel_py.codex_host.session import CodexSession


def thread_start_params(session: CodexSession) -> dict[str, Any]:
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
    # app-server 不持久化 MCP 配置，恢复线程时必须重新挂载并写入真实 thread id。
    # thread/resume 接受与 thread/start 相同的 permission override（openai/codex
    # app-server README）；不显式回传 cwd/sandbox/approvalPolicy，Codex 会回退默认
    # workspace-write/on-request，使原 Full access 会话恢复后权限回退。
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
        # approvalPolicy 接受 AskForApproval 字符串枚举（"untrusted"|"on-request"|"never"）。
        params["approvalPolicy"] = approval
    sandbox_policy = sandbox_policy_from_preset(sandbox)
    if sandbox_policy is not None:
        # turn/start 用 sandboxPolicy（SandboxPolicy 对象），不是 thread/start·resume
        # 的 sandbox（SandboxMode 字符串）；字段名与值都不能混用。
        params["sandboxPolicy"] = sandbox_policy
    return params


def sandbox_policy_from_preset(preset: str | None) -> dict[str, Any] | None:
    """把 preset 字符串映射到 turn/start 的 SandboxPolicy 对象。

    上游 codex app-server v2 ``SandboxPolicy`` 是 discriminated union：
    - ``dangerFullAccess`` 无额外字段；
    - ``readOnly`` 的 ``networkAccess`` 标 ``#[serde(default)]``，这里显式
      传 ``False``；
    - ``workspaceWrite`` 的 ``writableRoots``/``networkAccess``/
      ``excludeTmpdirEnvVar``/``excludeSlashTmp`` 全部 ``#[serde(default)]``
      （codex-rs app-server-protocol v2 ``permissions.rs``），缺失时服务端
      回退默认值。真实 app-server 实测：``dangerFullAccess`` thread 上只发
      ``approvalPolicy`` 不发 ``sandboxPolicy`` 时 native 仍沿用 danger，
      必须显式发 ``workspaceWrite`` policy 才真正降权；这里按实测的
      ``empty writableRoots / network false / exclude flags false`` 显式提供，
      避免依赖服务端默认行为。

    ``None`` 输入表示 ``follow`` preset，不发送 sandboxPolicy。
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
