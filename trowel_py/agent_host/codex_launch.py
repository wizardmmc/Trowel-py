"""装配 Codex session 的启动配置，不注册 manager 或持久化 binding。"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from trowel_py.agent_host.schemas import CreateAgentSessionRequest

if TYPE_CHECKING:
    from trowel_py.codex_host import CodexSession
    from trowel_py.resource_lifecycle import ResourceRegistry

_log = logging.getLogger("trowel_py.agent_host.hub")

_CODEX_PERMISSION_PRESETS: dict[str, tuple[str | None, str | None]] = {
    "follow": (None, None),
    "read-only": ("on-request", "read-only"),
    "workspace-write": ("on-request", "workspace-write"),
    "danger-full-access": ("never", "danger-full-access"),
}


@dataclass(frozen=True)
class PreparedCodexSession:
    """保存已经准备完成、等待 Session Hub 注册的 Codex 会话。

    Attributes:
        session_id: 准备阶段生成的 Trowel 会话 ID。
        session: 已配置启动参数、MCP 和轮次日志的 Codex 会话。
        permission_preset: 最终采用的权限模式；创建请求未指定时为 "follow"。
        injection_hash: 注入正文的内容指纹，不保存正文。
        declared_mcp_roster: Trowel 为该会话请求挂载的 MCP 名称。
    """

    session_id: str
    session: CodexSession
    permission_preset: str
    injection_hash: str
    declared_mcp_roster: tuple[str, ...]


def prepare_codex_session(
    req: CreateAgentSessionRequest,
    *,
    session_id_factory: Callable[[], str],
    permission_presets: Mapping[str, tuple[str | None, str | None]],
    fingerprint: Callable[[str], str],
    resource_registry: ResourceRegistry | None = None,
    memory_mcp_enabled: bool | None = None,
) -> PreparedCodexSession:
    """根据创建请求准备尚未注册的 Codex 会话及其绑定信息。

    Memory 或 Self 注入失败时降级为空；会话注册和绑定持久化由 Session Hub 完成。

    Args:
        req: 已校验的 Agent 会话创建请求。
        session_id_factory: 生成 Trowel 会话 ID 的函数。
        permission_presets: 权限模式到操作确认策略和沙箱模式的对应关系。
        fingerprint: 计算注入正文内容指纹的函数。
        resource_registry: 桌面实例资源账本；存在时为间接启动的 MCP 签发登记令牌。
        memory_mcp_enabled: 是否挂载 Memory MCP；None 时沿用正文注入开关。

    Returns:
        已配置完成、等待 Session Hub 注册的 Codex 会话。
    """
    from trowel_py.codex_host import CodexSession, CodexSessionConfig
    from trowel_py.codex_host.session import (
        build_default_trowel_agent_mcp,
        build_default_trowel_memory_mcp,
    )
    from trowel_py.application_paths import resolve_application_data_root
    from trowel_py.memory.injection import build_memory_injection
    from trowel_py.memory.codex_journal import CodexTurnJournal
    from trowel_py.memory.paths import resolve_memory_root
    from trowel_py.model_os.self_assembler import build_session_injection

    session_id = session_id_factory()
    preset = req.permission_preset or "follow"
    approval_policy, sandbox = permission_presets[preset]
    # 未选择权限模式时，继续接受旧接口直接传入的操作确认策略和沙箱模式。
    if req.permission_preset is None and (
        req.approval_policy is not None or req.sandbox is not None
    ):
        approval_policy = req.approval_policy
        sandbox = req.sandbox

    memory_root = resolve_memory_root()
    effective_memory_mcp = (
        req.memory_enabled if memory_mcp_enabled is None else memory_mcp_enabled
    )
    # Memory 内容生成失败时不阻止会话创建，改为空内容继续。
    try:
        memory_text = build_memory_injection(
            date.today().isoformat(),
            memory_root,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
        )
    except Exception:
        _log.warning(
            "memory injection failed; codex thread starts without memory section",
            exc_info=True,
        )
        memory_text = ""
    # Memory 内容为空时仍单独生成 Self，不能让 Memory 故障同时丢失身份信息。
    try:
        injection_text = build_session_injection(
            self_enabled=req.self_enabled,
            memory_text=memory_text,
            runtime="codex",
            model=req.model,
            effort=req.effort,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            permission_preset=preset,
        )
    except Exception:
        _log.warning(
            "self injection failed; codex thread starts without it",
            exc_info=True,
        )
        injection_text = ""

    # 先计算内容指纹；失败时不再构造 MCP，也不会返回可供注册和持久化的会话。
    injection_hash = fingerprint(injection_text)
    from trowel_py.resource_lifecycle import OwnerScope

    memory_registration_env = (
        resource_registry.issue_process_registration(
            owner_scope=OwnerScope.SESSION,
            resource_kind="codex_memory_mcp_process_group",
            ancestor_resource_kind="codex_app_server_process_group",
            runtime="codex",
            agent_session_id=session_id,
        )
        if resource_registry is not None and effective_memory_mcp
        else {}
    )
    agent_registration_env = (
        resource_registry.issue_process_registration(
            owner_scope=OwnerScope.SESSION,
            resource_kind="codex_agent_mcp_process_group",
            ancestor_resource_kind="codex_app_server_process_group",
            runtime="codex",
            agent_session_id=session_id,
        )
        if resource_registry is not None and req.agent_mcp_enabled
        else {}
    )
    # 关闭 Memory 时不挂载记忆检索 MCP，确保会话无法通过该工具读取 Memory。
    trowel_memory_mcp = (
        build_default_trowel_memory_mcp(
            trowel_session_id=session_id,
            memory_root=str(memory_root),
            application_data_root=str(resolve_application_data_root()),
            registration_env=memory_registration_env,
        )
        if effective_memory_mcp
        else None
    )
    port = os.environ.get("TROWEL_SERVER_PORT", "8000")
    trowel_agent_mcp = (
        build_default_trowel_agent_mcp(
            trowel_session_id=session_id,
            workdir=req.workdir,
            permission=preset,
            base_url=f"http://127.0.0.1:{port}",
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            self_enabled=req.self_enabled,
            delegation_depth=req.delegation_depth,
            registration_env=agent_registration_env,
        )
        if req.agent_mcp_enabled
        else None
    )
    declared_mcp_roster = tuple(
        config.server_name
        for config in (trowel_memory_mcp, trowel_agent_mcp)
        if config is not None
    )
    config = CodexSessionConfig(
        trowel_session_id=session_id,
        workdir=req.workdir,
        model=req.model,
        effort=req.effort,
        approval_policy=approval_policy,
        sandbox=sandbox,
        initial_thread_id=req.resume_from,
        developer_instructions=injection_text or None,
        trowel_memory_mcp=trowel_memory_mcp,
        trowel_agent_mcp=trowel_agent_mcp,
    )
    journal = CodexTurnJournal(
        memory_root,
        trowel_session_id=session_id,
        workdir=req.workdir,
        memory_enabled=req.memory_enabled,
        profile_enabled=req.profile_enabled,
        session_kind=req.session_kind,
    )
    session = CodexSession(config, event_sink=journal.record)
    return PreparedCodexSession(
        session_id=session_id,
        session=session,
        permission_preset=preset,
        injection_hash=injection_hash,
        declared_mcp_roster=declared_mcp_roster,
    )


def _injection_fingerprint(text: str) -> str:
    """生成供 binding 持久化的 48 位注入指纹；空正文保持空字符串。

    短指纹只用于变化比对，不作为唯一标识或安全摘要。
    """

    # TODO(refactor)：可提炼，在被调用的地方给上“生成供 binding 持久化的 48 位注入指纹；空正文保持空字符串。”这种语义解释
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
