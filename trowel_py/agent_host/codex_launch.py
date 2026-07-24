"""装配 Codex session 的启动配置，不注册 manager 或持久化 binding。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from trowel_py.agent_host.schemas import CreateAgentSessionRequest

if TYPE_CHECKING:
    from trowel_py.codex_host import CodexSession

_log = logging.getLogger("trowel_py.agent_host.hub")

_CODEX_PERMISSION_PRESETS: dict[str, tuple[str | None, str | None]] = {
    "follow": (None, None),
    "read-only": ("on-request", "read-only"),
    "workspace-write": ("on-request", "workspace-write"),
    "danger-full-access": ("never", "danger-full-access"),
}


@dataclass(frozen=True)
class PreparedCodexSession:
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
) -> PreparedCodexSession:
    from trowel_py.codex_host import CodexSession, CodexSessionConfig
    from trowel_py.codex_host.session import build_default_trowel_memory_mcp
    from trowel_py.memory.injection import build_memory_injection
    from trowel_py.memory.paths import resolve_memory_root
    from trowel_py.model_os.self_assembler import build_session_injection

    session_id = session_id_factory()
    preset = req.permission_preset or "follow"
    approval_policy, sandbox = permission_presets[preset]
    # 兼容仍直接传 approval_policy 与 sandbox 的旧调用者。
    if req.permission_preset is None and (
        req.approval_policy is not None or req.sandbox is not None
    ):
        approval_policy = req.approval_policy
        sandbox = req.sandbox

    memory_root = resolve_memory_root()
    # memory 文本组装失败时降级为空，仍继续创建会话。
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
    # Memory 为空或失败时仍尝试组装 Self，不能让笔记故障阻止主体上下文。
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

    # 指纹失败必须先于 MCP 构造、manager 注册和 binding 持久化。
    injection_hash = fingerprint(injection_text)
    # memory 关闭时不注册 Trowel MCP，关闭该工具读取路径。
    trowel_memory_mcp = (
        build_default_trowel_memory_mcp(
            trowel_session_id=session_id,
            memory_root=str(memory_root),
        )
        if req.memory_enabled
        else None
    )
    declared_mcp_roster = (trowel_memory_mcp.server_name,) if trowel_memory_mcp else ()
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
    )
    session = CodexSession(config)
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

    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
