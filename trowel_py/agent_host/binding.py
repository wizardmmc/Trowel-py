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
) -> SessionBinding:
    """创建 binding，并在同一时刻设置创建与更新时间。"""

    now = datetime.now().isoformat(timespec="seconds")
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
        created_at=now,
        updated_at=now,
    )


def binding_from_dict(data: dict[str, object]) -> SessionBinding:
    """兼容旧记录缺失的可选字段；必填字段和未知 runtime 仍严格失败。"""

    capabilities = data.get("capabilities", ())
    declared_mcp_roster = data.get("declared_mcp_roster", ())
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
    )
