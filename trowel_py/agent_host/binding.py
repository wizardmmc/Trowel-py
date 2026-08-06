"""Agent Host 的持久化记录及其公开会话投影。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, cast

from trowel_py.agent_host.capabilities import (
    CURRENT_CAPABILITY_VERSION,
    capabilities_for_runtime,
)

TitleSource = Literal["new", "native", "prompt", "generated", "manual"]
SessionKind = Literal["user", "delegate", "probe", "discussion"]
_TITLE_SOURCES: frozenset[str] = frozenset(
    {"new", "native", "prompt", "generated", "manual"}
)


class Runtime(str, Enum):
    """标识会话由 Claude Code 还是 Codex 运行。

    枚举值同时写入持久化文件和 API 数据，不能作为内部名称改写。
    """

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"


@dataclass(frozen=True)
class SessionBinding:
    """不可变的公开会话绑定。

    ``native_session_id`` 在原生 host 首次报告前可以为空。注入开关在恢复时保持
    不变；``injection_hash`` 只保存正文指纹，``declared_mcp_roster`` 只记录
    Trowel 声明的 MCP，不代表用户配置后的有效 roster。状态更新必须创建新实例，
    避免内存对象与落盘记录各自发生局部修改。

    Attributes:
        session_id: Trowel 分配并用于公开 API 的会话 ID。
        runtime: 当前会话由 Claude Code 还是 Codex 运行。
        native_session_id: Claude Code 会话 ID 或 Codex thread ID；原生 host 尚未
            报告时为 None。
        workdir: 会话冻结的完整工作目录。
        model: 当前请求或原生 host 回报的模型；尚未取得时为 None。
        effort: 当前思考强度；runtime 未提供时为 None。
        permission: 面向界面展示的有效权限摘要；尚未取得时为 None。
        memory_enabled: 是否向该会话注入 Trowel Memory。
        memory_mcp_enabled: 是否向该会话挂载 Memory MCP；与正文注入开关分离。
        profile_enabled: 是否向该会话注入用户画像。
        capabilities: 当前 Trowel 已实证并允许界面使用的 runtime 能力 ID。
        name: 多开栏使用的会话临时名称。
        capability_version: ``capabilities`` 所属的 Trowel 能力矩阵版本。
        checkpoint_available: 当前目录和本机配置是否允许实际创建 checkpoint；
            runtime 不支持或尚未确认时分别为 False 或 None。
        connected: Trowel 当前是否持有可继续使用的 runtime 连接。
        running: 当前会话是否有尚未结束的 turn。
        created_at: binding 首次创建的本地 ISO 时间。
        updated_at: binding 最近一次持久更新的本地 ISO 时间。
        permission_preset: 用户为 Codex 选择的权限预设；未选择时为 None。
        effective_permission_profile: Codex 当前生效的权限档位；未回报时为 None。
        effective_sandbox: Codex 当前生效的 sandbox；未回报时为 None。
        effective_approval: Codex 当前生效的审批策略；未回报时为 None。
        network_access: Codex 当前是否允许联网；未知或不适用时为 None。
        injection_hash: 本次注入正文的指纹，用于追溯而不持久化正文。
        declared_mcp_roster: Trowel 本次声明的 MCP 服务名，不包含用户配置扩展。
        self_enabled: 是否为 Model OS 会话注入 Self；普通用户会话通常为 True。
        session_kind: 区分用户会话、委派子会话和其他内部会话的持久标识。
        memory_eligibility: 该会话是否允许进入 Memory 提炼来源。
        agent_mcp_enabled: 是否向会话挂载跨 runtime 委派 MCP。
        parent_session_id: 委派子会话对应的 Trowel 父会话 ID；没有父会话时为 None。
        delegation_depth: 委派树中的层级，用户会话为 0。
        display_title: 用户可见的持久标题；尚未生成时为空字符串。
        title_source: 标题来自新建、原生记录、首条消息、模型生成还是手动修改。
        connection_id: 创建时冻结的设置域连接 ID；旧会话未知时为 None。
        connection_identity_version: 创建时冻结的连接启动身份版本。
        connection_name: 创建时冻结的脱敏连接展示名。
        connection_kind: 创建时冻结的连接种类。
        configuration_capability_version: 放行连接组合的设置域能力表版本。
        configuration_capability_source: 放行连接组合的真实验证证据说明。
        owner_ref: 内部会话的持久归属键；discussion participant 用它在应用重启后
            认领已经创建但尚未回写领域库的原生会话。
        requested_model: 创建请求冻结的模型选择；``model`` 可被 runtime 回写为有效 ID。
        requested_effort: 创建请求冻结的思考强度；``effort`` 可被 runtime 补成默认值。
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
    memory_mcp_enabled: bool = True
    capability_version: int = CURRENT_CAPABILITY_VERSION
    checkpoint_available: bool | None = None
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
    session_kind: SessionKind = "user"
    memory_eligibility: bool = True
    agent_mcp_enabled: bool = True
    parent_session_id: str | None = None
    delegation_depth: int = 0
    display_title: str = ""
    title_source: TitleSource = "new"
    connection_id: str | None = None
    connection_identity_version: int | None = None
    connection_name: str | None = None
    connection_kind: str | None = None
    configuration_capability_version: str | None = None
    configuration_capability_source: str | None = None
    owner_ref: str | None = None
    requested_model: str | None = None
    requested_effort: str | None = None

    def to_dict(self) -> dict[str, object]:
        """转换为可持久化的字典。"""

        return {
            "session_id": self.session_id,
            "runtime": self.runtime.value,
            "native_session_id": self.native_session_id,
            "workdir": self.workdir,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "memory_enabled": self.memory_enabled,
            "memory_mcp_enabled": self.memory_mcp_enabled,
            "profile_enabled": self.profile_enabled,
            "capabilities": list(self.capabilities),
            "capability_version": self.capability_version,
            "checkpoint_available": self.checkpoint_available,
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
            "memory_eligibility": self.memory_eligibility,
            "agent_mcp_enabled": self.agent_mcp_enabled,
            "parent_session_id": self.parent_session_id,
            "delegation_depth": self.delegation_depth,
            "display_title": self.display_title,
            "title_source": self.title_source,
            "connection_id": self.connection_id,
            "connection_identity_version": self.connection_identity_version,
            "connection_name": self.connection_name,
            "connection_kind": self.connection_kind,
            "configuration_capability_version": (self.configuration_capability_version),
            "configuration_capability_source": self.configuration_capability_source,
            "owner_ref": self.owner_ref,
            "requested_model": self.requested_model,
            "requested_effort": self.requested_effort,
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
    memory_mcp_enabled: bool | None = None,
    capability_version: int = CURRENT_CAPABILITY_VERSION,
    checkpoint_available: bool | None = None,
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
    session_kind: SessionKind = "user",
    memory_eligibility: bool = True,
    agent_mcp_enabled: bool = True,
    parent_session_id: str | None = None,
    delegation_depth: int = 0,
    display_title: str = "",
    title_source: TitleSource = "new",
    connection_id: str | None = None,
    connection_identity_version: int | None = None,
    connection_name: str | None = None,
    connection_kind: str | None = None,
    configuration_capability_version: str | None = None,
    configuration_capability_source: str | None = None,
    owner_ref: str | None = None,
) -> SessionBinding:
    """创建 binding，并在同一时刻设置创建与更新时间。

    Args:
        session_id: Trowel 分配的公开会话 ID。
        runtime: 会话冻结使用的原生 runtime。
        native_session_id: 原生会话或 thread ID；尚未报告时为 None。
        workdir: 会话冻结的完整工作目录。
        model: 创建时请求的模型；沿用 runtime 默认值时为 None。
        effort: 创建时请求的思考强度；沿用默认值时为 None。
        permission: 面向界面的有效权限摘要；尚未取得时为 None。
        memory_enabled: 是否注入 Trowel Memory。
        memory_mcp_enabled: 是否挂载 Memory MCP；None 时沿用 Memory 正文开关。
        profile_enabled: 是否注入用户画像。
        capabilities: 已实证并允许界面使用的 runtime 能力 ID。
        name: 多开栏使用的会话临时名称。
        capability_version: capabilities 对应的能力矩阵版本。
        checkpoint_available: 当前目录和本机配置是否允许实际创建 checkpoint。
        connected: 创建 binding 时是否已经建立可用连接。
        running: 创建 binding 时是否已经存在未结束 turn。
        permission_preset: 用户选择的 Codex 权限预设。
        effective_permission_profile: Codex 已确认生效的权限档位。
        effective_sandbox: Codex 已确认生效的 sandbox。
        effective_approval: Codex 已确认生效的审批策略。
        network_access: Codex 已确认的联网权限。
        injection_hash: 注入正文的指纹。
        declared_mcp_roster: Trowel 声明的 MCP 服务名。
        self_enabled: 是否注入 Model OS Self。
        session_kind: 用户、委派或其他内部会话的类别。
        memory_eligibility: 是否允许该会话进入 Memory 提炼。
        agent_mcp_enabled: 是否挂载跨 runtime 委派 MCP。
        parent_session_id: 委派子会话的 Trowel 父会话 ID。
        delegation_depth: 委派树层级，用户会话为 0。
        display_title: 当前用户可见标题。
        title_source: 当前标题的来源。
        connection_id: 设置域稳定连接 ID；旧会话未知时为 None。
        connection_identity_version: 冻结的连接启动身份版本。
        connection_name: 冻结的脱敏连接展示名。
        connection_kind: 冻结的连接种类。
        configuration_capability_version: 设置域能力表版本。
        configuration_capability_source: 设置域能力结论的实测来源。
        owner_ref: 内部会话的稳定归属键；普通用户会话为 None。

    Returns:
        带统一创建时间和更新时间的不可变 binding。
    """

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
        memory_mcp_enabled=(
            memory_enabled if memory_mcp_enabled is None else memory_mcp_enabled
        ),
        profile_enabled=profile_enabled,
        capabilities=tuple(capabilities),
        name=name,
        capability_version=capability_version,
        checkpoint_available=checkpoint_available,
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
        memory_eligibility=memory_eligibility,
        agent_mcp_enabled=agent_mcp_enabled,
        parent_session_id=parent_session_id,
        delegation_depth=delegation_depth,
        display_title=display_title,
        title_source=title_source,
        connection_id=connection_id,
        connection_identity_version=connection_identity_version,
        connection_name=connection_name,
        connection_kind=connection_kind,
        configuration_capability_version=configuration_capability_version,
        configuration_capability_source=configuration_capability_source,
        owner_ref=owner_ref,
        requested_model=model,
        requested_effort=effort,
        created_at=now,
        updated_at=now,
    )


def binding_from_dict(data: dict[str, object]) -> SessionBinding:
    """把持久化字典恢复为 binding，并升级旧能力清单。

    Args:
        data: BindingStore 读出的单个会话字典；旧记录可以缺少后来新增的可选字段。

    Returns:
        经过默认值兼容和能力矩阵升级的不可变 binding。

    Raises:
        KeyError: 记录缺少会话身份、runtime、目录或名称等必填字段。
        ValueError: 记录包含当前不支持的 runtime。
    """

    runtime = Runtime(str(data["runtime"]))
    raw_connection_identity_version = data.get("connection_identity_version")
    raw_capability_version = data.get("capability_version")
    if (
        isinstance(raw_capability_version, int)
        and not isinstance(raw_capability_version, bool)
        and raw_capability_version >= CURRENT_CAPABILITY_VERSION
    ):
        capability_version = raw_capability_version
        capabilities = data.get("capabilities", ())
    else:
        capability_version = CURRENT_CAPABILITY_VERSION
        capabilities = capabilities_for_runtime(runtime.value)
    declared_mcp_roster = data.get("declared_mcp_roster", ())
    raw_delegation_depth = data.get("delegation_depth", 0)
    delegation_depth = (
        raw_delegation_depth
        if isinstance(raw_delegation_depth, int)
        and not isinstance(raw_delegation_depth, bool)
        else 0
    )
    raw_title_source = data.get("title_source", "new")
    title_source: TitleSource = (
        cast(TitleSource, raw_title_source)
        if isinstance(raw_title_source, str) and raw_title_source in _TITLE_SOURCES
        else "new"
    )
    raw_checkpoint_available = data.get("checkpoint_available")
    checkpoint_available = (
        raw_checkpoint_available if isinstance(raw_checkpoint_available, bool) else None
    )
    has_connection = data.get("connection_id") is not None
    memory_enabled = bool(data.get("memory_enabled", True))
    return SessionBinding(
        session_id=str(data["session_id"]),
        runtime=runtime,
        native_session_id=(
            str(data["native_session_id"]) if data.get("native_session_id") else None
        ),
        workdir=str(data["workdir"]),
        model=str(data["model"]) if data.get("model") is not None else None,
        effort=str(data["effort"]) if data.get("effort") is not None else None,
        permission=(
            str(data["permission"]) if data.get("permission") is not None else None
        ),
        memory_enabled=memory_enabled,
        memory_mcp_enabled=bool(
            data.get("memory_mcp_enabled", memory_enabled and not has_connection)
        ),
        profile_enabled=bool(data.get("profile_enabled", True)),
        capabilities=tuple(str(c) for c in capabilities)  # type: ignore[arg-type]
        if isinstance(capabilities, (list, tuple))
        else (),
        name=str(data["name"]),
        capability_version=capability_version,
        checkpoint_available=checkpoint_available,
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
        session_kind=cast(SessionKind, str(data.get("session_kind", "user"))),
        memory_eligibility=bool(data.get("memory_eligibility", True)),
        agent_mcp_enabled=bool(data.get("agent_mcp_enabled", True)),
        parent_session_id=(
            str(data["parent_session_id"])
            if data.get("parent_session_id") is not None
            else None
        ),
        delegation_depth=delegation_depth,
        display_title=(
            str(data["display_title"])
            if isinstance(data.get("display_title"), str)
            else ""
        ),
        title_source=title_source,
        connection_id=(
            str(data["connection_id"])
            if data.get("connection_id") is not None
            else None
        ),
        connection_identity_version=(
            raw_connection_identity_version
            if isinstance(raw_connection_identity_version, int)
            and not isinstance(raw_connection_identity_version, bool)
            else None
        ),
        connection_name=(
            str(data["connection_name"])
            if data.get("connection_name") is not None
            else None
        ),
        connection_kind=(
            str(data["connection_kind"])
            if data.get("connection_kind") is not None
            else None
        ),
        configuration_capability_version=(
            str(data["configuration_capability_version"])
            if data.get("configuration_capability_version") is not None
            else None
        ),
        configuration_capability_source=(
            str(data["configuration_capability_source"])
            if data.get("configuration_capability_source") is not None
            else None
        ),
        owner_ref=(
            str(data["owner_ref"]) if data.get("owner_ref") is not None else None
        ),
        requested_model=(
            (
                str(data["requested_model"])
                if data.get("requested_model") is not None
                else None
            )
            if "requested_model" in data
            else (str(data["model"]) if data.get("model") is not None else None)
        ),
        requested_effort=(
            (
                str(data["requested_effort"])
                if data.get("requested_effort") is not None
                else None
            )
            if "requested_effort" in data
            else (str(data["effort"]) if data.get("effort") is not None else None)
        ),
    )
