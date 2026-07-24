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
    if config.trowel_memory_mcp is not None:
        params["config"] = {"mcp_servers": config.trowel_memory_mcp.to_thread_config()}
    return params


def thread_resume_params(session: CodexSession) -> dict[str, Any]:
    # app-server 不持久化 MCP 配置，恢复线程时必须重新挂载并写入真实 thread id。
    assert session.binding is not None
    params: dict[str, Any] = {"threadId": session.binding.thread_id}
    if session.config.trowel_memory_mcp is not None:
        params["config"] = {
            "mcp_servers": session.config.trowel_memory_mcp.to_thread_config(
                native_session_id=session.binding.thread_id
            )
        }
    return params


def turn_start_params(
    thread_id: str,
    text: str,
    *,
    model: str | None = None,
    effort: str | None = None,
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
    return params
