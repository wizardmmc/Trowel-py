"""在进程内登记临时资源，并原子发布 Host 可读的去敏快照。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from trowel_py.resource_lifecycle.models import (
    OwnerScope,
    OwnerCloseObservation,
    OwnerSummary,
    ProcessIdentity,
    ResourceRecord,
    ResourceState,
    ReconcileReport,
)
from trowel_py.resource_lifecycle.processes import (
    DescendantInventory,
    LocalProcessController,
    ProcessController,
    list_descendant_processes,
)

SNAPSHOT_VERSION = 1
RECENT_CLOSED_LIMIT = 256
RECENT_CLOSED_OWNER_LIMIT = 256
NowFn = Callable[[], datetime]
SnapshotWriter = Callable[[Path, dict[str, object]], None]
OwnerCloseObserver = Callable[[OwnerCloseObservation], None]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ProcessRegistrationGrant:
    """把一个不可猜测令牌绑定到固定资源 owner 和可信启动树。

    Attributes:
        owner_scope: 子进程直接归属的生命周期层级。
        resource_kind: 资源摘要和关闭策略使用的子进程类型。
        ancestor_resource_kind: 账本中可信根进程的类型；上报 PID 必须能沿实时
            PPID 链追溯到这个根进程。
        runtime: 启动子进程的原生运行时；不适用时为 None。
        runtime_generation: 已知的运行时连接序号；签发时尚未连接则为 0，登记时
            从可信根进程补齐。
        runtime_connection_id: 已知的连接 owner ID；签发时尚未连接则为 None，
            登记时从可信根进程补齐。
        agent_session_id: session 或 turn 资源归属的 Trowel 会话 ID。
        turn_id: turn 资源归属的原生轮次 ID；其他层级为 None。
        parent_resource_id: 已知的直接上游资源 ID；尚未启动时为 None，登记时
            使用可信根进程资源 ID。
    """

    owner_scope: OwnerScope
    resource_kind: str
    ancestor_resource_kind: str
    runtime: str | None
    runtime_generation: int
    runtime_connection_id: str | None
    agent_session_id: str | None
    turn_id: str | None
    parent_resource_id: str | None


def redact_identity(value: str) -> str:
    """把本机资源或 owner ID 转成稳定的不可逆短摘要。

    Args:
        value: 不能原样写入 Host 快照的内部 ID。
    """

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


class ResourceRegistry:
    """维护单个应用实例创建的临时资源及其关闭状态。"""

    def __init__(
        self,
        *,
        app_instance_id: str,
        snapshot_path: Path | None = None,
        process_controller: ProcessController | None = None,
        now: NowFn | None = None,
        descendant_inventory: DescendantInventory = list_descendant_processes,
        registration_url: str | None = None,
        registration_credential: str | None = None,
        snapshot_writer: SnapshotWriter | None = None,
        owner_close_observer: OwnerCloseObserver | None = None,
    ) -> None:
        """创建资源账本并发布初始空快照。

        Args:
            app_instance_id: 当前应用启动的稳定实例 ID；不能为空。
            snapshot_path: Host 和下次启动 reaper 读取的 JSON 路径；None 表示只
                在进程内登记，适用于普通 browser 模式。
            process_controller: 读取和核验进程身份的实现；省略时使用本机进程表。
            now: 生成资源状态时间的时钟；省略时使用本地当前时间。
            descendant_inventory: 按可信根 PID 读取实时 PPID 后代身份的函数。
            registration_url: Trowel 自有子进程回报 PID 的桌面私有端点。
            registration_credential: 调用私有端点所需的当前桌面实例凭据。
            snapshot_writer: 原子快照写入函数；测试可注入只记录发布次数的替身。
            owner_close_observer: owner 成功关闭后的非阻塞观测回调。

        Raises:
            ValueError: 应用实例 ID 为空。
        """

        normalized_instance_id = app_instance_id.strip()
        if not normalized_instance_id:
            raise ValueError("resource registry requires an app instance id")
        self._app_instance_id = normalized_instance_id
        self._snapshot_path = snapshot_path
        self._process_controller = process_controller or LocalProcessController()
        self._now = now or (lambda: datetime.now().astimezone())
        self._descendant_inventory = descendant_inventory
        self._registration_url = (registration_url or "").strip()
        self._registration_credential = (registration_credential or "").strip()
        self._registration_grants: dict[str, _ProcessRegistrationGrant] = {}
        self._records: dict[str, ResourceRecord] = {}
        self._recent_closed: OrderedDict[str, ResourceRecord] = OrderedDict()
        self._closing_owners: set[tuple[OwnerScope, str | None, str | None]] = set()
        self._recent_closed_owners: OrderedDict[
            tuple[OwnerScope, str | None, str | None], None
        ] = OrderedDict()
        self._owner_closing_started_at: dict[
            tuple[OwnerScope, str | None, str | None], datetime
        ] = {}
        self._snapshot_writer = snapshot_writer or _atomic_write_json
        self._owner_close_observer = owner_close_observer
        self._lock = threading.RLock()
        self._publish_snapshot()

    @property
    def app_instance_id(self) -> str:
        """返回当前账本所属的原始应用实例 ID。"""

        return self._app_instance_id

    @property
    def snapshot_path(self) -> Path | None:
        """返回 Host 快照路径；进程内模式为 None。"""

        return self._snapshot_path

    @property
    def process_controller(self) -> ProcessController:
        """返回账本用于核验进程身份和终止进程组的控制器。"""

        return self._process_controller

    @property
    def registration_credential(self) -> str:
        """返回本应用自有子进程访问本地私有 API 使用的桌面凭据。"""

        return self._registration_credential

    def set_owner_close_observer(
        self,
        observer: OwnerCloseObserver | None,
    ) -> None:
        """替换 owner 关闭观测回调，不改变任何资源状态。

        资源账本可以先于遥测启动，避免遥测已启动但账本创建失败时留下后台线程。

        Args:
            observer: 新的非阻塞观测回调；None 表示停止上报。
        """

        with self._lock:
            self._owner_close_observer = observer

    def issue_process_registration(
        self,
        *,
        owner_scope: OwnerScope,
        resource_kind: str,
        ancestor_resource_kind: str,
        runtime: str | None = None,
        runtime_generation: int = 0,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
        parent_resource_id: str | None = None,
    ) -> dict[str, str]:
        """为间接启动的 Trowel 子进程签发绑定 owner 和可信启动树的环境。

        普通 browser 模式没有桌面凭据和私有端点，此时返回空映射；子进程无需回报。

        Args:
            owner_scope: 子进程结束时服从的生命周期层级。
            resource_kind: 子进程写入诊断摘要的资源类型。
            ancestor_resource_kind: Trowel 直接启动并登记的可信根进程类型；上报
                PID 必须是这个根进程的实时后代。
            runtime: 启动子进程的原生运行时；不适用时为 None。
            runtime_generation: 已知的运行时连接序号；连接尚未启动时保留 0。
            runtime_connection_id: 已知的连接 owner ID；连接尚未启动时为 None。
            agent_session_id: session 或 turn 资源所属的 Trowel 会话 ID。
            turn_id: turn 资源所属的原生轮次 ID；其他层级为 None。
            parent_resource_id: 已知的上游资源 ID；尚未启动时为 None。

        Returns:
            注入子进程的私有端点、桌面凭据和一次性随机令牌；browser 模式为空。
        """

        normalized_ancestor_kind = ancestor_resource_kind.strip()
        if not normalized_ancestor_kind:
            raise ValueError("process registration requires an ancestor resource kind")
        if not self._registration_url or not self._registration_credential:
            return {}
        grant = _ProcessRegistrationGrant(
            owner_scope=owner_scope,
            resource_kind=resource_kind,
            ancestor_resource_kind=normalized_ancestor_kind,
            runtime=runtime,
            runtime_generation=runtime_generation,
            runtime_connection_id=runtime_connection_id,
            agent_session_id=agent_session_id,
            turn_id=turn_id,
            parent_resource_id=parent_resource_id,
        )
        self._validate(
            ResourceRecord(
                resource_id="registration-grant",
                app_instance_id=self._app_instance_id,
                owner_scope=owner_scope,
                resource_kind=resource_kind,
                state=ResourceState.RUNNING,
                created_at=self._stamp(),
                updated_at=self._stamp(),
                runtime=runtime,
                runtime_generation=runtime_generation,
                runtime_connection_id=runtime_connection_id,
                agent_session_id=agent_session_id,
                turn_id=turn_id,
                parent_resource_id=parent_resource_id,
            )
        )
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._registration_grants[token] = grant
        return {
            "TROWEL_RESOURCE_REGISTRATION_URL": self._registration_url,
            "TROWEL_RESOURCE_REGISTRATION_CREDENTIAL": self._registration_credential,
            "TROWEL_RESOURCE_REGISTRATION_TOKEN": token,
        }

    def register_reported_process(self, token: str, *, pid: int) -> ResourceRecord:
        """核验上报 PID 的实时启动树，再按令牌绑定的 owner 登记进程组。

        Args:
            token: 启动配置中签发给目标子进程的随机登记令牌。
            pid: 子进程自报的当前进程 ID。

        Returns:
            已写入账本且补齐当前连接归属的进程资源。

        Raises:
            ValueError: 令牌未知、PID 不存在、可信根不唯一，或 PID 不属于可信根
                的实时后代。
        """

        with self._lock:
            grant = self._registration_grants.get(token)
        if grant is None:
            raise ValueError("unknown resource registration token")
        identity = self._process_controller.inspect(pid)
        if identity is None:
            raise ValueError("reported resource process is not running")
        parent_record = self._resolve_reporting_ancestor(grant, identity)
        if identity.process_group == parent_record.process_group:
            raise ValueError("reported resource requires an independent process group")
        resolved_grant = replace(
            grant,
            runtime_generation=parent_record.runtime_generation,
            runtime_connection_id=parent_record.runtime_connection_id,
            parent_resource_id=parent_record.resource_id,
        )
        resource_id = (
            f"reported:{redact_identity(token)}:"
            f"{redact_identity(identity.start_identity)}"
        )
        with self._lock:
            existing = self._records.get(resource_id)
            if existing is not None and existing.state is not ResourceState.CLOSED:
                return existing
        return self.register_process_group(
            resource_id=resource_id,
            owner_scope=grant.owner_scope,
            resource_kind=resolved_grant.resource_kind,
            pid=pid,
            runtime=resolved_grant.runtime,
            runtime_generation=resolved_grant.runtime_generation,
            runtime_connection_id=resolved_grant.runtime_connection_id,
            agent_session_id=resolved_grant.agent_session_id,
            turn_id=resolved_grant.turn_id,
            parent_resource_id=resolved_grant.parent_resource_id,
        )

    def _resolve_reporting_ancestor(
        self,
        grant: _ProcessRegistrationGrant,
        reported_identity: ProcessIdentity,
    ) -> ResourceRecord:
        """返回唯一且身份仍匹配的可信根进程，并确认上报 PID 是其实时后代。

        Args:
            grant: 令牌预先绑定的 owner、runtime 和可信根进程条件。
            reported_identity: 已从当前进程表读取的上报 PID 身份。

        Returns:
            同时满足预签发条件、启动身份和实时 PPID 关系的根进程记录。

        Raises:
            ValueError: 没有可信根，存在多个匹配根，或上报进程不属于该启动树。
        """

        with self._lock:
            candidates = tuple(
                record
                for record in self._records.values()
                if record.state is ResourceState.RUNNING
                and record.resource_kind == grant.ancestor_resource_kind
                and record.runtime == grant.runtime
                and (
                    grant.runtime_connection_id is None
                    or record.runtime_connection_id == grant.runtime_connection_id
                )
                and (
                    grant.runtime_generation == 0
                    or record.runtime_generation == grant.runtime_generation
                )
                and (
                    grant.parent_resource_id is None
                    or record.resource_id == grant.parent_resource_id
                )
                and record.pid is not None
                and record.process_group is not None
                and record.process_start_identity is not None
            )
        matching: list[ResourceRecord] = []
        for candidate in candidates:
            assert candidate.pid is not None
            current_root = self._process_controller.inspect(candidate.pid)
            if (
                current_root is None
                or current_root.process_group != candidate.process_group
                or current_root.start_identity != candidate.process_start_identity
            ):
                continue
            descendants = self._descendant_inventory(candidate.pid)
            if reported_identity in descendants:
                matching.append(candidate)
        if len(matching) != 1:
            raise ValueError(
                "reported resource is not in one trusted runtime process tree"
            )
        return matching[0]

    def register_handle(
        self,
        *,
        resource_id: str,
        owner_scope: OwnerScope,
        resource_kind: str,
        runtime: str | None = None,
        runtime_generation: int = 0,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
        parent_resource_id: str | None = None,
        connection_id: str | None = None,
        lease_expires_at: str | None = None,
    ) -> ResourceRecord:
        """登记没有独立 PID 的连接、thread、watcher 或 pending handle。

        Args:
            resource_id: 进程内幂等定位该资源的稳定 ID。
            owner_scope: 直接决定关闭时机的 owner 层级。
            resource_kind: 诊断和清理策略使用的资源类型。
            runtime: Claude Code、Codex 或应用内部资源；不适用时为 None。
            runtime_generation: 当前 runtime 进程或连接序号。
            runtime_connection_id: 连接 owner 的内部 ID。
            agent_session_id: session 或 turn 所属的 Trowel 会话 ID。
            turn_id: turn scope 对应的原生轮次 ID。
            parent_resource_id: 启动该资源的上游资源 ID。
            connection_id: 上游暴露的 thread 或连接 handle。
            lease_expires_at: 需要租约时保存的 ISO 到期时间。

        Returns:
            已登记的不可变资源记录。
        """

        stamp = self._stamp()
        record = ResourceRecord(
            resource_id=resource_id,
            owner_scope=owner_scope,
            app_instance_id=self._app_instance_id,
            resource_kind=resource_kind,
            state=ResourceState.RUNNING,
            created_at=stamp,
            updated_at=stamp,
            runtime=runtime,
            runtime_generation=runtime_generation,
            runtime_connection_id=runtime_connection_id,
            agent_session_id=agent_session_id,
            turn_id=turn_id,
            parent_resource_id=parent_resource_id,
            connection_id=connection_id,
            lease_expires_at=lease_expires_at,
        )
        return self._register(record)

    def register_process_group(
        self,
        *,
        resource_id: str,
        owner_scope: OwnerScope,
        resource_kind: str,
        pid: int,
        runtime: str | None = None,
        runtime_generation: int = 0,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
        parent_resource_id: str | None = None,
        lease_expires_at: str | None = None,
    ) -> ResourceRecord:
        """核对进程启动身份后登记一个独立进程组。

        Args:
            resource_id: 进程内幂等定位该进程组的稳定 ID。
            owner_scope: 直接决定关闭时机的 owner 层级。
            resource_kind: 诊断和清理策略使用的进程组类型。
            pid: 独立进程组根进程的 PID。
            runtime: Claude Code、Codex 或应用内部资源；不适用时为 None。
            runtime_generation: 当前 runtime 进程或连接序号。
            runtime_connection_id: 连接 owner 的内部 ID。
            agent_session_id: session 或 turn 所属的 Trowel 会话 ID。
            turn_id: turn scope 对应的原生轮次 ID。
            parent_resource_id: 启动该进程组的上游资源 ID。
            lease_expires_at: 需要租约时保存的 ISO 到期时间。

        Returns:
            含 PID、进程组和启动指纹的资源记录。

        Raises:
            RuntimeError: spawn 返回的 PID 已经不存在或无法读取身份。
        """

        identity = self._process_controller.inspect(pid)
        if identity is None:
            raise RuntimeError(f"cannot identify spawned process {pid}")
        stamp = self._stamp()
        record = ResourceRecord(
            resource_id=resource_id,
            owner_scope=owner_scope,
            app_instance_id=self._app_instance_id,
            resource_kind=resource_kind,
            state=ResourceState.RUNNING,
            created_at=stamp,
            updated_at=stamp,
            runtime=runtime,
            runtime_generation=runtime_generation,
            runtime_connection_id=runtime_connection_id,
            agent_session_id=agent_session_id,
            turn_id=turn_id,
            parent_resource_id=parent_resource_id,
            pid=identity.pid,
            process_group=identity.process_group,
            process_start_identity=identity.start_identity,
            lease_expires_at=lease_expires_at,
        )
        return self._register(record)

    def get(self, resource_id: str) -> ResourceRecord:
        """返回指定资源记录。

        Args:
            resource_id: 登记资源时使用的进程内 ID。

        Raises:
            KeyError: 资源从未登记。
        """

        with self._lock:
            record = self._records.get(resource_id)
            if record is not None:
                return record
            return self._recent_closed[resource_id]

    def mark_owner_closing(
        self,
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """把 owner 及其更短生命周期子资源标记为关闭中。

        Args:
            owner_scope: 开始结束的 owner 层级。
            runtime_connection_id: runtime connection owner 的内部 ID。
            agent_session_id: session 或 turn owner 的 Trowel 会话 ID。
            turn_id: turn owner 的原生轮次 ID。
        """

        with self._lock:
            owner_key = self._owner_key(
                owner_scope,
                runtime_connection_id=runtime_connection_id,
                agent_session_id=agent_session_id,
                turn_id=turn_id,
            )
            self._closing_owners.add(owner_key)
            self._owner_closing_started_at.setdefault(owner_key, self._now())
            self._revoke_owner_registration_grants(
                owner_scope,
                runtime_connection_id=runtime_connection_id,
                agent_session_id=agent_session_id,
                turn_id=turn_id,
            )
            stamp = self._stamp()
            changed = False
            for resource_id, record in tuple(self._records.items()):
                if record.state is ResourceState.CLOSING:
                    continue
                if self._belongs_to_owner(
                    record,
                    owner_scope,
                    runtime_connection_id=runtime_connection_id,
                    agent_session_id=agent_session_id,
                    turn_id=turn_id,
                ):
                    self._records[resource_id] = replace(
                        record,
                        state=ResourceState.CLOSING,
                        updated_at=stamp,
                    )
                    changed = True
            if changed:
                self._publish_snapshot()

    def mark_owner_closed(
        self,
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """把已经核验归零的 owner 及其子资源统一提交为 closed。

        Args:
            owner_scope: 已完成关闭的 owner 层级。
            runtime_connection_id: runtime connection owner 的内部 ID。
            agent_session_id: session 或 turn owner 的 Trowel 会话 ID。
            turn_id: turn owner 的原生轮次 ID。
        """

        observation: OwnerCloseObservation | None = None
        with self._lock:
            owner_key = self._owner_key(
                owner_scope,
                runtime_connection_id=runtime_connection_id,
                agent_session_id=agent_session_id,
                turn_id=turn_id,
            )
            was_closing = owner_key in self._closing_owners
            self._closing_owners.add(owner_key)
            completed_at = self._now()
            started_at = self._owner_closing_started_at.get(owner_key, completed_at)
            stamp = completed_at.isoformat(timespec="microseconds")
            changed = False
            closed_resource_count = 0
            for resource_id, record in tuple(self._records.items()):
                if self._belongs_to_owner(
                    record,
                    owner_scope,
                    runtime_connection_id=runtime_connection_id,
                    agent_session_id=agent_session_id,
                    turn_id=turn_id,
                ):
                    closed = replace(
                        record,
                        state=ResourceState.CLOSED,
                        updated_at=stamp,
                        last_error=None,
                    )
                    self._records.pop(resource_id, None)
                    self._remember_closed(closed)
                    changed = True
                    closed_resource_count += 1
            self._release_owner_barriers(
                owner_scope,
                runtime_connection_id=runtime_connection_id,
                agent_session_id=agent_session_id,
                turn_id=turn_id,
            )
            if changed:
                self._publish_snapshot()
            if changed or was_closing:
                observation = OwnerCloseObservation(
                    owner_scope=owner_scope,
                    status="closed",
                    started_at=started_at,
                    completed_at=completed_at,
                    closed_resource_count=closed_resource_count,
                    remaining_resource_count=0,
                )
        if observation is not None:
            self._observe_owner_close(observation)

    def mark_owner_needs_reconcile(
        self,
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
        remaining_resource_count: int | None = None,
    ) -> None:
        """提交 owner 未归零终态，并保留关闭屏障等待后续重试。

        Args:
            owner_scope: 本次关闭未能归零的 owner 层级。
            runtime_connection_id: runtime connection owner 的内部 ID。
            agent_session_id: session 或 turn owner 的 Trowel 会话 ID。
            turn_id: turn owner 的原生轮次 ID。
            remaining_resource_count: runtime 另行确认但未必已经登记的残留数量。

        Raises:
            ValueError: 外部残留数量为负数。
        """

        if remaining_resource_count is not None and remaining_resource_count < 0:
            raise ValueError("remaining resource count must not be negative")
        with self._lock:
            owner_key = self._owner_key(
                owner_scope,
                runtime_connection_id=runtime_connection_id,
                agent_session_id=agent_session_id,
                turn_id=turn_id,
            )
            self._closing_owners.add(owner_key)
            completed_at = self._now()
            started_at = self._owner_closing_started_at.setdefault(
                owner_key,
                completed_at,
            )
            stamp = completed_at.isoformat(timespec="microseconds")
            matched_count = 0
            changed = False
            for resource_id, record in tuple(self._records.items()):
                if not self._belongs_to_owner(
                    record,
                    owner_scope,
                    runtime_connection_id=runtime_connection_id,
                    agent_session_id=agent_session_id,
                    turn_id=turn_id,
                ):
                    continue
                matched_count += 1
                next_record = replace(
                    record,
                    state=ResourceState.NEEDS_RECONCILE,
                    updated_at=stamp,
                    last_error="owner close did not reach zero",
                )
                if next_record == record:
                    continue
                self._records[resource_id] = next_record
                changed = True
            if changed:
                self._publish_snapshot()
            observation = OwnerCloseObservation(
                owner_scope=owner_scope,
                status="needs_reconcile",
                started_at=started_at,
                completed_at=completed_at,
                closed_resource_count=0,
                remaining_resource_count=max(
                    matched_count,
                    remaining_resource_count or 0,
                ),
            )
        self._observe_owner_close(observation)

    def mark_closed(self, resource_id: str) -> None:
        """把已核验退出的资源标为 closed；重复调用保持幂等。

        Args:
            resource_id: 要提交关闭终态的资源 ID。
        """

        self._mark_records_closed((resource_id,))

    def mark_needs_reconcile(self, resource_id: str, error: str) -> None:
        """记录资源无法安全确认关闭，并保存有界错误说明。

        Args:
            resource_id: 关闭失败的资源 ID。
            error: 不含凭据或正文的错误说明。
        """

        self._mark_records_needs_reconcile({resource_id: error})

    def owner_summary(
        self,
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
    ) -> OwnerSummary:
        """汇总 owner 及其子资源是否已经归零。

        Args:
            owner_scope: 要汇总的 owner 层级。
            runtime_connection_id: runtime connection owner 的内部 ID。
            agent_session_id: session 或 turn owner 的 Trowel 会话 ID。
            turn_id: turn owner 的原生轮次 ID。
        """

        with self._lock:
            live = [
                record
                for record in self._records.values()
                if record.state is not ResourceState.CLOSED
                and self._belongs_to_owner(
                    record,
                    owner_scope,
                    runtime_connection_id=runtime_connection_id,
                    agent_session_id=agent_session_id,
                    turn_id=turn_id,
                )
            ]
        if not live:
            status = ResourceState.CLOSED.value
        elif any(item.state is ResourceState.NEEDS_RECONCILE for item in live):
            status = ResourceState.NEEDS_RECONCILE.value
        elif any(item.state is ResourceState.CLOSING for item in live):
            status = ResourceState.CLOSING.value
        else:
            status = ResourceState.RUNNING.value
        return OwnerSummary(
            status=status,
            live_resource_count=len(live),
            remaining_resource_kinds=tuple(
                sorted({record.resource_kind for record in live})
            ),
        )

    def private_summary(self) -> dict[str, object]:
        """返回桌面私有端点使用的去身份化资源数量摘要。"""

        with self._lock:
            live = [
                record
                for record in self._records.values()
                if record.state is not ResourceState.CLOSED
            ]
        states: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for record in live:
            states[record.state.value] = states.get(record.state.value, 0) + 1
            kinds[record.resource_kind] = kinds.get(record.resource_kind, 0) + 1
        return {
            "app_instance_id": redact_identity(self._app_instance_id),
            "live_resource_count": len(live),
            "states": states,
            "kinds": kinds,
        }

    def reconcile_process_groups(
        self,
        *,
        excluded_resource_kinds: frozenset[str] = frozenset(),
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        term_wait_seconds: float = 3.0,
        kill_wait_seconds: float = 1.0,
        poll_interval_seconds: float = 0.05,
    ) -> ReconcileReport:
        """收敛当前账本中身份仍匹配的独立进程组。

        Args:
            excluded_resource_kinds: 由外层 Host 负责、当前进程不能终止的资源类型。
            runtime_connection_id: 只处理指定 runtime 连接；None 表示不过滤。
            agent_session_id: 只处理指定 Agent 会话；None 表示不过滤。
            term_wait_seconds: SIGTERM 后等待进程组退出的秒数。
            kill_wait_seconds: SIGKILL 后等待进程组退出的秒数。
            poll_interval_seconds: 两次存活检查之间的秒数。

        Returns:
            已关闭、身份不匹配、仍存活和错误数量组成的报告。
        """

        with self._lock:
            process_records = tuple(
                record
                for record in self._records.values()
                if record.state is not ResourceState.CLOSED
                and record.resource_kind not in excluded_resource_kinds
                and record.pid is not None
                and record.process_group is not None
                and record.process_start_identity is not None
                and (
                    runtime_connection_id is None
                    or record.runtime_connection_id == runtime_connection_id
                )
                and (
                    agent_session_id is None
                    or record.agent_session_id == agent_session_id
                )
            )
        records_by_group: dict[int, list[ResourceRecord]] = {}
        for record in process_records:
            assert record.process_group is not None
            records_by_group.setdefault(record.process_group, []).append(record)

        groups: dict[int, list[ResourceRecord]] = {}
        already_gone = 0
        identity_mismatch = 0
        unverified_groups: set[int] = set()
        gone_resource_ids: list[str] = []
        unverified_resources: dict[str, str] = {}
        for process_group, records in records_by_group.items():
            if not self._process_controller.group_alive(process_group):
                already_gone += 1
                gone_resource_ids.extend(record.resource_id for record in records)
                continue

            group_verified = False
            saw_changed_identity = False
            for record in records:
                assert record.pid is not None
                current = self._process_controller.inspect(record.pid)
                if current is None:
                    continue
                if (
                    current.process_group == process_group
                    and current.start_identity == record.process_start_identity
                ):
                    group_verified = True
                    break
                saw_changed_identity = True
            if group_verified:
                groups[process_group] = records
                continue

            identity_mismatch += 1
            unverified_groups.add(process_group)
            error = (
                "process identity changed before application drain"
                if saw_changed_identity
                else "process root unavailable while group remains alive"
            )
            unverified_resources.update(
                {record.resource_id: error for record in records}
            )

        self._mark_records_closed(gone_resource_ids)
        self._mark_records_needs_reconcile(unverified_resources)

        errors: list[str] = []
        signaled = self._signal_groups(groups, "SIGTERM", errors)
        remaining = self._wait_groups(
            signaled,
            timeout=term_wait_seconds,
            poll_interval=poll_interval_seconds,
        )
        self._close_finished_groups(groups, signaled - remaining)
        remaining |= set(groups) - signaled
        killed = self._signal_groups(
            {group: groups[group] for group in remaining},
            "SIGKILL",
            errors,
        )
        kill_remaining = self._wait_groups(
            killed,
            timeout=kill_wait_seconds,
            poll_interval=poll_interval_seconds,
        )
        self._close_finished_groups(groups, killed - kill_remaining)
        remaining = (remaining - killed) | kill_remaining
        self._mark_records_needs_reconcile(
            {
                record.resource_id: "process group survived application drain"
                for group in remaining
                for record in groups[group]
            }
        )
        return ReconcileReport(
            terminated=len(set(groups) - remaining),
            already_gone=already_gone,
            identity_mismatch=identity_mismatch,
            remaining=len(remaining | unverified_groups),
            errors=tuple(errors),
        )

    def _signal_groups(
        self,
        groups: dict[int, list[ResourceRecord]],
        signal_name: str,
        errors: list[str],
    ) -> set[int]:
        """向进程组集合发送信号，并返回成功发出的组。"""

        signaled: set[int] = set()
        failed_resources: dict[str, str] = {}
        for process_group in sorted(groups):
            try:
                self._process_controller.signal_group(process_group, signal_name)
                signaled.add(process_group)
            except (ProcessLookupError, PermissionError, OSError, RuntimeError) as exc:
                errors.append(f"{signal_name} process group: {type(exc).__name__}")
                failed_resources.update(
                    {
                        record.resource_id: (
                            f"process-group signal failed: {type(exc).__name__}"
                        )
                        for record in groups[process_group]
                    }
                )
        self._mark_records_needs_reconcile(failed_resources)
        return signaled

    def _wait_groups(
        self,
        groups: set[int],
        *,
        timeout: float,
        poll_interval: float,
    ) -> set[int]:
        """在截止时间后返回仍存活的进程组。"""

        deadline = time.monotonic() + max(timeout, 0.0)
        remaining = {
            group for group in groups if self._process_controller.group_alive(group)
        }
        while remaining and time.monotonic() < deadline:
            if poll_interval > 0:
                time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))
            remaining = {
                group
                for group in remaining
                if self._process_controller.group_alive(group)
            }
        return remaining

    def _close_finished_groups(
        self,
        groups: dict[int, list[ResourceRecord]],
        finished: set[int],
    ) -> None:
        """把已确认退出进程组对应的全部资源记录提交为 closed。"""

        self._mark_records_closed(
            tuple(
                record.resource_id
                for process_group in finished
                for record in groups[process_group]
            )
        )

    def _mark_records_closed(self, resource_ids: tuple[str, ...] | list[str]) -> None:
        """把一批已核验资源一次提交为 closed，并最多发布一次快照。

        Args:
            resource_ids: 已确认关闭的内部资源 ID。
        """

        if not resource_ids:
            return
        with self._lock:
            stamp = self._stamp()
            changed = False
            for resource_id in resource_ids:
                record = self._records.pop(resource_id, None)
                if record is None:
                    continue
                self._remember_closed(
                    replace(
                        record,
                        state=ResourceState.CLOSED,
                        updated_at=stamp,
                        last_error=None,
                    )
                )
                changed = True
            if changed:
                self._publish_snapshot()

    def _mark_records_needs_reconcile(
        self,
        errors_by_resource: dict[str, str],
    ) -> None:
        """把一批关闭失败一次提交为 needs_reconcile。

        Args:
            errors_by_resource: 内部资源 ID 到去敏错误分类的映射。
        """

        if not errors_by_resource:
            return
        with self._lock:
            stamp = self._stamp()
            changed = False
            for resource_id, error in errors_by_resource.items():
                record = self._records.get(resource_id)
                if record is None:
                    continue
                next_record = replace(
                    record,
                    state=ResourceState.NEEDS_RECONCILE,
                    updated_at=stamp,
                    last_error=error[:500],
                )
                if next_record == record:
                    continue
                self._records[resource_id] = next_record
                changed = True
            if changed:
                self._publish_snapshot()

    def _register(self, record: ResourceRecord) -> ResourceRecord:
        """校验 owner 字段后登记资源，并拒绝 ID 指向另一活资源。"""

        self._validate(record)
        with self._lock:
            if self._owner_is_closing(record):
                raise RuntimeError(
                    f"resource owner is closing: {record.owner_scope.value}"
                )
            existing = self._records.get(record.resource_id)
            if existing is not None:
                if existing == record:
                    return existing
                raise ValueError(
                    f"live resource id already registered: {record.resource_id}"
                )
            self._recent_closed.pop(record.resource_id, None)
            self._records[record.resource_id] = record
            self._publish_snapshot()
            return record

    def _owner_is_closing(self, record: ResourceRecord) -> bool:
        """判断资源的 app、connection、session 或 turn owner 是否正在关闭。"""

        candidates = {
            self._owner_key(OwnerScope.APP),
            self._owner_key(
                OwnerScope.RUNTIME_CONNECTION,
                runtime_connection_id=record.runtime_connection_id,
            ),
            self._owner_key(
                OwnerScope.SESSION,
                agent_session_id=record.agent_session_id,
            ),
            self._owner_key(
                OwnerScope.TURN,
                agent_session_id=record.agent_session_id,
                turn_id=record.turn_id,
            ),
        }
        return not self._closing_owners.isdisjoint(candidates) or any(
            candidate in self._recent_closed_owners for candidate in candidates
        )

    def _revoke_owner_registration_grants(
        self,
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None,
        agent_session_id: str | None,
        turn_id: str | None,
    ) -> None:
        """owner 开始关闭时撤销尚未使用或可能重启进程的登记令牌。"""

        for token, grant in tuple(self._registration_grants.items()):
            if owner_scope is OwnerScope.APP:
                self._registration_grants.pop(token, None)
            elif (
                owner_scope is OwnerScope.RUNTIME_CONNECTION
                and grant.runtime_connection_id == runtime_connection_id
            ):
                self._registration_grants.pop(token, None)
            elif (
                owner_scope is OwnerScope.SESSION
                and grant.agent_session_id == agent_session_id
            ):
                self._registration_grants.pop(token, None)
            elif (
                owner_scope is OwnerScope.TURN
                and grant.agent_session_id == agent_session_id
                and grant.turn_id == turn_id
            ):
                self._registration_grants.pop(token, None)

    def _remember_closed(self, record: ResourceRecord) -> None:
        """把最近关闭记录放入有界诊断缓存，不再进入 crash 快照。

        Args:
            record: 已确认进入 closed 终态的内部资源记录。
        """

        self._recent_closed[record.resource_id] = record
        self._recent_closed.move_to_end(record.resource_id)
        while len(self._recent_closed) > RECENT_CLOSED_LIMIT:
            self._recent_closed.popitem(last=False)

    def _observe_owner_close(self, observation: OwnerCloseObservation) -> None:
        """隔离 owner 观测回调失败，关闭事实不能反向阻塞资源终态。

        Args:
            observation: 已提交关闭且不含 owner 身份的事实。
        """

        observer = self._owner_close_observer
        if observer is None:
            return
        try:
            observer(observation)
        except Exception:
            logger.debug("resource owner telemetry failed", exc_info=True)

    def _release_owner_barriers(
        self,
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None,
        agent_session_id: str | None,
        turn_id: str | None,
    ) -> None:
        """owner 关闭提交后释放临时登记屏障，避免键集合无限增长。

        Args:
            owner_scope: 已提交关闭的 owner 层级。
            runtime_connection_id: runtime connection owner 的内部 ID。
            agent_session_id: session 或 turn owner 的 Trowel 会话 ID。
            turn_id: turn owner 的原生轮次 ID。
        """

        if owner_scope is OwnerScope.APP:
            released = tuple(self._closing_owners)
            self._closing_owners.clear()
            self._owner_closing_started_at.clear()
            for key in released:
                self._remember_closed_owner(key)
            self._remember_closed_owner(self._owner_key(OwnerScope.APP))
            return
        if owner_scope is OwnerScope.SESSION:
            released = tuple(
                key
                for key in self._closing_owners
                if key[1] == agent_session_id
                and key[0] in {OwnerScope.SESSION, OwnerScope.TURN}
            )
            self._closing_owners = {
                key
                for key in self._closing_owners
                if not (
                    key[1] == agent_session_id
                    and key[0] in {OwnerScope.SESSION, OwnerScope.TURN}
                )
            }
            self._owner_closing_started_at = {
                key: started_at
                for key, started_at in self._owner_closing_started_at.items()
                if not (
                    key[1] == agent_session_id
                    and key[0] in {OwnerScope.SESSION, OwnerScope.TURN}
                )
            }
            for key in released:
                if key[0] is OwnerScope.TURN:
                    self._remember_closed_owner(key)
            self._remember_closed_owner(
                self._owner_key(
                    OwnerScope.SESSION,
                    agent_session_id=agent_session_id,
                )
            )
            return
        owner_key = self._owner_key(
            owner_scope,
            runtime_connection_id=runtime_connection_id,
            agent_session_id=agent_session_id,
            turn_id=turn_id,
        )
        self._closing_owners.discard(owner_key)
        self._owner_closing_started_at.pop(owner_key, None)
        self._remember_closed_owner(owner_key)

    def _remember_closed_owner(
        self,
        owner_key: tuple[OwnerScope, str | None, str | None],
    ) -> None:
        """保留近期 owner 终态屏障，并淘汰更旧的唯一身份。

        Args:
            owner_key: 不写入磁盘的 owner 层级与内部身份组合。
        """

        self._recent_closed_owners[owner_key] = None
        self._recent_closed_owners.move_to_end(owner_key)
        while len(self._recent_closed_owners) > RECENT_CLOSED_OWNER_LIMIT:
            self._recent_closed_owners.popitem(last=False)

    @staticmethod
    def _owner_key(
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None = None,
        agent_session_id: str | None = None,
        turn_id: str | None = None,
    ) -> tuple[OwnerScope, str | None, str | None]:
        """把不同层级 owner 转成可比较的内部键。"""

        if owner_scope is OwnerScope.APP:
            return owner_scope, None, None
        if owner_scope is OwnerScope.RUNTIME_CONNECTION:
            return owner_scope, runtime_connection_id, None
        if owner_scope is OwnerScope.SESSION:
            return owner_scope, agent_session_id, None
        return owner_scope, agent_session_id, turn_id

    @staticmethod
    def _validate(record: ResourceRecord) -> None:
        """检查各 owner scope 只要求真实存在的身份字段。"""

        if not record.resource_id or not record.resource_kind:
            raise ValueError("resource_id and resource_kind are required")
        if (
            record.owner_scope is OwnerScope.RUNTIME_CONNECTION
            and not record.runtime_connection_id
        ):
            raise ValueError("runtime_connection owner requires runtime_connection_id")
        if record.owner_scope in {OwnerScope.SESSION, OwnerScope.TURN} and not (
            record.agent_session_id
        ):
            raise ValueError("session and turn owners require agent_session_id")
        if record.owner_scope is OwnerScope.TURN and not record.turn_id:
            raise ValueError("turn owner requires turn_id")

    @staticmethod
    def _belongs_to_owner(
        record: ResourceRecord,
        owner_scope: OwnerScope,
        *,
        runtime_connection_id: str | None,
        agent_session_id: str | None,
        turn_id: str | None,
    ) -> bool:
        """判断资源是否属于指定 owner 或其更短生命周期子级。"""

        if owner_scope is OwnerScope.APP:
            return True
        if owner_scope is OwnerScope.RUNTIME_CONNECTION:
            return bool(runtime_connection_id) and (
                record.runtime_connection_id == runtime_connection_id
            )
        if owner_scope is OwnerScope.SESSION:
            return bool(agent_session_id) and (
                record.agent_session_id == agent_session_id
            )
        return bool(agent_session_id and turn_id) and (
            record.agent_session_id == agent_session_id and record.turn_id == turn_id
        )

    def _stamp(self) -> str:
        """返回当前时钟的微秒级 ISO 文本。"""

        return self._now().isoformat(timespec="microseconds")

    def _publish_snapshot(self) -> None:
        """把当前资源表原子写成只含去敏 owner 和进程身份的 JSON。"""

        path = self._snapshot_path
        if path is None:
            return
        payload = {
            "version": SNAPSHOT_VERSION,
            "app_instance_id": redact_identity(self._app_instance_id),
            "updated_at": self._stamp(),
            "resources": [
                self._snapshot_record(record) for record in self._records.values()
            ],
        }
        self._snapshot_writer(path, payload)

    @staticmethod
    def _snapshot_record(record: ResourceRecord) -> dict[str, object]:
        """把一条内部记录转换为 Host 只读的去敏形态。"""

        payload: dict[str, object] = {
            "resource_id": redact_identity(record.resource_id),
            "owner_scope": record.owner_scope.value,
            "owner_id": redact_identity(
                record.turn_id
                or record.agent_session_id
                or record.runtime_connection_id
                or record.app_instance_id
            ),
            "resource_kind": record.resource_kind,
            "runtime": record.runtime,
            "runtime_generation": record.runtime_generation,
            "state": record.state.value,
            "updated_at": record.updated_at,
        }
        if record.pid is not None:
            payload["pid"] = record.pid
        if record.process_group is not None:
            payload["process_group"] = record.process_group
        if record.process_start_identity is not None:
            payload["process_start_identity"] = record.process_start_identity
        return payload


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    """写完并同步临时文件后原子替换资源快照。

    Args:
        path: 最终快照路径。
        payload: 已完成去敏的 JSON 对象。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
