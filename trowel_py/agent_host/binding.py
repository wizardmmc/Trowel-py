"""Agent Host 的持久化记录及其公开会话投影。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Runtime(str, Enum):
    """枚举值同时写入持久化文件和 API wire shape，不能作为内部名称改写。"""

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"


@dataclass(frozen=True)
class RuntimeIdentity:
    """controller 恢复与精确清理所需的 runtime 进程/会话身份。"""

    agent_session_id: str
    runtime: str
    native_session_id: str | None
    runtime_generation: str
    runtime_pid: int | None = None
    runtime_pgid: int | None = None


@dataclass(frozen=True)
class SessionBinding:
    """不可变的公开会话绑定。

    ``native_session_id`` 在原生 host 首次报告前可以为空。注入开关在恢复时保持
    不变；``injection_hash`` 只保存正文指纹，``declared_mcp_roster`` 只记录
    Trowel 声明的 MCP，不代表用户配置后的有效 roster。状态更新必须创建新实例，
    避免内存对象与落盘记录各自发生局部修改。
    """

    session_id: str
    runtime: Runtime
    native_session_id: str | None
    workdir: str
    model: str | None
    effort: str | None
    permission: str | None
    memory_enabled: bool
    profile_enabled: bool
    capabilities: tuple[str, ...]
    name: str
    connected: bool = False
    running: bool = False
    created_at: str = ""
    updated_at: str = ""
    permission_preset: str | None = None
    effective_permission_profile: str | None = None
    effective_sandbox: str | None = None
    effective_approval: str | None = None
    network_access: bool | None = None
    injection_hash: str = ""
    declared_mcp_roster: tuple[str, ...] = ()
    self_enabled: bool = True
    session_kind: str = "user"
    session_purpose: str = "foreground"
    memory_eligibility: bool = True
    memory_eligibility_mode: str = "eligible"
    agent_mcp_enabled: bool = True
    model_os_mcp_enabled: bool = False
    native_tools_mode: str = "default"
    parent_session_id: str | None = None
    delegation_depth: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "runtime": self.runtime.value,
            "native_session_id": self.native_session_id,
            "workdir": self.workdir,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "memory_enabled": self.memory_enabled,
            "profile_enabled": self.profile_enabled,
            "capabilities": list(self.capabilities),
            "name": self.name,
            "connected": self.connected,
            "running": self.running,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "permission_preset": self.permission_preset,
            "effective_permission_profile": self.effective_permission_profile,
            "effective_sandbox": self.effective_sandbox,
            "effective_approval": self.effective_approval,
            "network_access": self.network_access,
            "injection_hash": self.injection_hash,
            "declared_mcp_roster": list(self.declared_mcp_roster),
            "self_enabled": self.self_enabled,
            "session_kind": self.session_kind,
            "session_purpose": self.session_purpose,
            "memory_eligibility": self.memory_eligibility,
            "memory_eligibility_mode": self.memory_eligibility_mode,
            "agent_mcp_enabled": self.agent_mcp_enabled,
            "model_os_mcp_enabled": self.model_os_mcp_enabled,
            "native_tools_mode": self.native_tools_mode,
            "parent_session_id": self.parent_session_id,
            "delegation_depth": self.delegation_depth,
        }


def make_binding(
    *,
    session_id: str,
    runtime: Runtime,
    native_session_id: str | None,
    workdir: str,
    model: str | None,
    effort: str | None,
    permission: str | None,
    memory_enabled: bool,
    profile_enabled: bool,
    capabilities: Iterable[str],
    name: str,
    connected: bool = False,
    running: bool = False,
    permission_preset: str | None = None,
    effective_permission_profile: str | None = None,
    effective_sandbox: str | None = None,
    effective_approval: str | None = None,
    network_access: bool | None = None,
    injection_hash: str = "",
    declared_mcp_roster: Iterable[str] = (),
    self_enabled: bool = True,
    session_kind: str = "user",
    session_purpose: str = "foreground",
    memory_eligibility: bool = True,
    memory_eligibility_mode: str = "eligible",
    agent_mcp_enabled: bool = True,
    model_os_mcp_enabled: bool = False,
    native_tools_mode: str = "default",
    parent_session_id: str | None = None,
    delegation_depth: int = 0,
) -> SessionBinding:
    """创建 binding，并在同一时刻设置创建与更新时间。"""

    now = datetime.now().isoformat(timespec="microseconds")
    return SessionBinding(
        session_id=session_id,
        runtime=runtime,
        native_session_id=native_session_id,
        workdir=workdir,
        model=model,
        effort=effort,
        permission=permission,
        memory_enabled=memory_enabled,
        profile_enabled=profile_enabled,
        capabilities=tuple(capabilities),
        name=name,
        connected=connected,
        running=running,
        permission_preset=permission_preset,
        effective_permission_profile=effective_permission_profile,
        effective_sandbox=effective_sandbox,
        effective_approval=effective_approval,
        network_access=network_access,
        injection_hash=injection_hash,
        declared_mcp_roster=tuple(declared_mcp_roster),
        self_enabled=self_enabled,
        session_kind=session_kind,
        session_purpose=session_purpose,
        memory_eligibility=memory_eligibility,
        memory_eligibility_mode=memory_eligibility_mode,
        agent_mcp_enabled=agent_mcp_enabled,
        model_os_mcp_enabled=model_os_mcp_enabled,
        native_tools_mode=native_tools_mode,
        parent_session_id=parent_session_id,
        delegation_depth=delegation_depth,
        created_at=now,
        updated_at=now,
    )


def binding_from_dict(data: dict[str, object]) -> SessionBinding:
    """兼容旧记录缺失的可选字段；必填字段和未知 runtime 仍严格失败。"""

    capabilities = data.get("capabilities", ())
    declared_mcp_roster = data.get("declared_mcp_roster", ())
    raw_delegation_depth = data.get("delegation_depth", 0)
    delegation_depth = (
        raw_delegation_depth
        if isinstance(raw_delegation_depth, int)
        and not isinstance(raw_delegation_depth, bool)
        else 0
    )
    return SessionBinding(
        session_id=str(data["session_id"]),
        runtime=Runtime(str(data["runtime"])),
        native_session_id=(
            str(data["native_session_id"]) if data.get("native_session_id") else None
        ),
        workdir=str(data["workdir"]),
        model=str(data["model"]) if data.get("model") is not None else None,
        effort=str(data["effort"]) if data.get("effort") is not None else None,
        permission=(
            str(data["permission"]) if data.get("permission") is not None else None
        ),
        memory_enabled=bool(data.get("memory_enabled", True)),
        profile_enabled=bool(data.get("profile_enabled", True)),
        capabilities=tuple(str(c) for c in capabilities)  # type: ignore[arg-type]
        if isinstance(capabilities, (list, tuple))
        else (),
        name=str(data["name"]),
        connected=bool(data.get("connected", False)),
        running=bool(data.get("running", False)),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        permission_preset=(
            str(data["permission_preset"])
            if data.get("permission_preset") is not None
            else None
        ),
        effective_permission_profile=(
            str(data["effective_permission_profile"])
            if data.get("effective_permission_profile") is not None
            else None
        ),
        effective_sandbox=(
            str(data["effective_sandbox"])
            if data.get("effective_sandbox") is not None
            else None
        ),
        effective_approval=(
            str(data["effective_approval"])
            if data.get("effective_approval") is not None
            else None
        ),
        network_access=(
            bool(data["network_access"])
            if data.get("network_access") is not None
            else None
        ),
        injection_hash=str(data.get("injection_hash", "")),
        declared_mcp_roster=tuple(str(s) for s in declared_mcp_roster)
        if isinstance(declared_mcp_roster, (list, tuple))
        else (),
        self_enabled=bool(data.get("self_enabled", True)),
        session_kind=str(data.get("session_kind", "user")),
        session_purpose=str(data.get("session_purpose", "foreground")),
        memory_eligibility=bool(data.get("memory_eligibility", True)),
        memory_eligibility_mode=str(
            data.get(
                "memory_eligibility_mode",
                "eligible" if data.get("memory_eligibility", True) else "ineligible",
            )
        ),
        agent_mcp_enabled=bool(data.get("agent_mcp_enabled", True)),
        model_os_mcp_enabled=bool(data.get("model_os_mcp_enabled", False)),
        native_tools_mode=str(data.get("native_tools_mode", "default")),
        parent_session_id=(
            str(data["parent_session_id"])
            if data.get("parent_session_id") is not None
            else None
        ),
        delegation_depth=delegation_depth,
    )
