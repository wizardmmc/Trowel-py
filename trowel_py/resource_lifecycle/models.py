"""定义应用临时资源、进程身份和收敛报告。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


class OwnerScope(str, Enum):
    """标识哪个生命周期结束时必须清理资源。"""

    APP = "app"
    RUNTIME_CONNECTION = "runtime_connection"
    SESSION = "session"
    TURN = "turn"


class ResourceState(str, Enum):
    """记录临时资源从可用到完成回收的状态。"""

    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"
    NEEDS_RECONCILE = "needs_reconcile"


@dataclass(frozen=True)
class ProcessIdentity:
    """保存发送信号前必须重新核对的进程身份。

    Attributes:
        pid: 进程组根进程的操作系统进程 ID。
        process_group: 终止整棵已知进程树时使用的进程组 ID。
        start_identity: 从启动时间和可执行文件计算的不可逆指纹，用于识别 PID
            是否已经被新进程复用。
    """

    pid: int
    process_group: int
    start_identity: str


@dataclass(frozen=True)
class ResourceRecord:
    """记录一个由 Trowel 生命周期直接负责的临时资源。

    Attributes:
        resource_id: 进程内定位和幂等更新该资源的稳定 ID。
        owner_scope: 决定资源何时开始关闭的直接 owner 层级。
        app_instance_id: 创建资源的应用实例 ID。
        resource_kind: 面向诊断和关闭策略的资源类型。
        state: 资源当前处于运行、关闭中、已关闭还是等待修复。
        created_at: 资源首次登记的本地 ISO 时间。
        updated_at: 最近一次状态变化的本地 ISO 时间。
        runtime: 资源属于 Claude Code、Codex 或应用自身；不适用时为 None。
        runtime_generation: runtime 重连或进程重启后递增的序号。
        runtime_connection_id: 连接级 owner 的内部 ID；其他 scope 可为空。
        agent_session_id: session 和 turn 资源所属的 Trowel 会话 ID。
        turn_id: turn 资源所属的原生轮次 ID。
        parent_resource_id: 启动本资源的上游资源 ID；未知时为 None。
        pid: 进程组根进程 ID；纯连接或原生 handle 资源为 None。
        process_group: 终止进程树使用的进程组 ID；非进程资源为 None。
        process_start_identity: 防止 PID 复用误杀的启动指纹；非进程资源为 None。
        connection_id: 上游只暴露连接或 thread handle 时保存的内部 ID。
        lease_expires_at: 需要租约的资源到期时间；没有租约时为 None。
        last_error: 最近一次关闭失败的去敏说明；没有错误时为 None。
    """

    resource_id: str
    owner_scope: OwnerScope
    app_instance_id: str
    resource_kind: str
    state: ResourceState
    created_at: str
    updated_at: str
    runtime: str | None = None
    runtime_generation: int = 0
    runtime_connection_id: str | None = None
    agent_session_id: str | None = None
    turn_id: str | None = None
    parent_resource_id: str | None = None
    pid: int | None = None
    process_group: int | None = None
    process_start_identity: str | None = None
    connection_id: str | None = None
    lease_expires_at: str | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class OwnerSummary:
    """汇总一个 owner 尚未完成回收的资源。

    Attributes:
        status: owner 当前为 running、closing、closed 或 needs_reconcile。
        live_resource_count: 尚未处于 closed 状态的资源数量。
        remaining_resource_kinds: 尚未关闭的资源类型，按名称排序且去重。
    """

    status: str
    live_resource_count: int
    remaining_resource_kinds: tuple[str, ...]


@dataclass(frozen=True)
class OwnerCloseObservation:
    """描述一个资源 owner 已提交关闭的低基数事实。

    Attributes:
        owner_scope: app、runtime connection、session 或 turn。
        status: closed 表示已经归零；needs_reconcile 表示仍需继续清理。
        started_at: owner 首次进入 closing 的墙钟时刻。
        completed_at: 本次关闭核验得出终态的墙钟时刻。
        closed_resource_count: 本次从活资源表移出的资源数。
        remaining_resource_count: 当前终态仍残留的资源数。
    """

    owner_scope: OwnerScope
    status: Literal["closed", "needs_reconcile"]
    started_at: datetime
    completed_at: datetime
    closed_resource_count: int
    remaining_resource_count: int


@dataclass(frozen=True)
class ReconcileReport:
    """说明启动 reaper 对上一应用实例残留资源的处理结果。

    Attributes:
        terminated: 身份匹配且已经确认退出的进程组数量。
        already_gone: 检查时已经不存在的进程组数量。
        identity_mismatch: PID 已复用或进程组不匹配而拒绝终止的数量。
        remaining: 升级到强制结束后仍存活的已确认进程组数量。
        errors: 读取或发送信号时产生的去敏错误说明。
        skipped_current_instance: 快照属于当前实例，因此没有执行清理。
        skipped_data_root_mismatch: 快照来自另一数据根，因此拒绝继承其进程终止权限。
    """

    terminated: int = 0
    already_gone: int = 0
    identity_mismatch: int = 0
    remaining: int = 0
    errors: tuple[str, ...] = ()
    skipped_current_instance: bool = False
    skipped_data_root_mismatch: bool = False
