"""协调会话、后台调度器和共享 runtime 的幂等应用退出。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from trowel_py.resource_lifecycle.models import OwnerScope
from trowel_py.resource_lifecycle.registry import ResourceRegistry


@dataclass(frozen=True)
class DrainReport:
    """记录一次应用 cooperative drain 的去敏结果。

    Attributes:
        status: `closed` 表示 sidecar 自身之外的临时资源已归零；否则需要 Host
            升级终止或下次启动 reconcile。
        session_closed: 已确认 runtime 资源归零的实时会话数。
        session_needs_reconcile: 仍有资源或关闭异常的实时会话数。
        live_resource_count: 账本中仍未 closed 的资源总数，包含正在响应的 sidecar。
        remaining_resource_kinds: 仍未 closed 的资源类型。
        errors: 按组件记录的去敏错误类型。
    """

    status: Literal["closed", "needs_reconcile"]
    session_closed: int
    session_needs_reconcile: int
    live_resource_count: int
    remaining_resource_kinds: tuple[str, ...]
    errors: tuple[str, ...]

    def to_private_dict(self) -> dict[str, object]:
        """返回桌面私有端点可直接编码且不含会话身份的字典。"""

        return {
            "status": self.status,
            "session_closed": self.session_closed,
            "session_needs_reconcile": self.session_needs_reconcile,
            "live_resource_count": self.live_resource_count,
            "remaining_resource_kinds": list(self.remaining_resource_kinds),
            "errors": list(self.errors),
        }


class DrainCoordinator:
    """把一次应用退出序列收敛成可被多个调用方复用的 task。"""

    def __init__(
        self,
        *,
        resource_registry: ResourceRegistry,
        agent_hub: Any | None,
        pre_session_components: tuple[tuple[str, Any], ...] = (),
        schedulers: tuple[tuple[str, Any], ...] = (),
        codex_manager: Any | None = None,
    ) -> None:
        """保存关闭顺序涉及的组件，不在构造时产生副作用。

        Args:
            resource_registry: 当前应用实例的临时资源账本。
            agent_hub: 统一关闭实时 Agent 会话的 Hub；未初始化时为 None。
            pre_session_components: 必须先禁止调度、再由 Hub 关会话的 owner 组件。
            schedulers: `(诊断名称, 组件)` 元组；组件须提供异步 `stop()`。
            codex_manager: 会话归零后关闭的共享 Codex app-server 管理器。
        """

        self._resource_registry = resource_registry
        self._agent_hub = agent_hub
        self._pre_session_components = pre_session_components
        self._schedulers = schedulers
        self._codex_manager = codex_manager
        self._drain_task: asyncio.Task[DrainReport] | None = None

    @property
    def draining(self) -> bool:
        """返回退出序列是否已经开始。"""

        return self._drain_task is not None

    async def drain(self) -> DrainReport:
        """开始或复用同一次退出序列，并屏蔽单个调用方取消。"""

        if self._drain_task is None:
            self._drain_task = asyncio.create_task(
                self._run(),
                name="trowel-app-drain",
            )
        return await asyncio.shield(self._drain_task)

    async def _run(self) -> DrainReport:
        """按 owner、会话、调度器、共享 runtime 的顺序执行一次关闭。"""

        self._resource_registry.mark_owner_closing(OwnerScope.APP)
        session_results: dict[str, Any] = {}
        errors: list[str] = []
        owner_outcomes = await asyncio.gather(
            *(
                self._stop_component(name, component)
                for name, component in self._pre_session_components
            )
        )
        errors.extend(error for error in owner_outcomes if error is not None)
        if self._agent_hub is not None:
            try:
                session_results = await self._agent_hub.close_all()
            except BaseException as exc:  # noqa: BLE001 - 仍需继续关闭其他组件。
                errors.append(f"agent_hub:{type(exc).__name__}")

        scheduler_outcomes = await asyncio.gather(
            *(self._stop_component(name, scheduler) for name, scheduler in self._schedulers)
        )
        errors.extend(error for error in scheduler_outcomes if error is not None)

        if self._codex_manager is not None:
            error = await self._close_codex_manager()
            if error is not None:
                errors.append(error)

        process_report = await asyncio.to_thread(
            self._resource_registry.reconcile_process_groups,
            excluded_resource_kinds=frozenset({"sidecar_process_group"}),
        )
        errors.extend(process_report.errors)

        summary = self._resource_registry.private_summary()
        raw_kinds = summary.get("kinds")
        kind_items = raw_kinds.items() if isinstance(raw_kinds, dict) else ()
        kinds = {
            str(kind): int(count)
            for kind, count in kind_items
            if isinstance(count, int) and not isinstance(count, bool)
        }
        raw_live_resource_count = summary.get("live_resource_count")
        live_resource_count = (
            raw_live_resource_count
            if isinstance(raw_live_resource_count, int)
            and not isinstance(raw_live_resource_count, bool)
            else 0
        )
        internal_live = live_resource_count - kinds.get("sidecar_process_group", 0)
        needs_reconcile = sum(
            result.status != "closed" for result in session_results.values()
        )
        status: Literal["closed", "needs_reconcile"] = (
            "closed"
            if internal_live == 0 and needs_reconcile == 0 and not errors
            else "needs_reconcile"
        )
        return DrainReport(
            status=status,
            session_closed=sum(
                result.status == "closed" for result in session_results.values()
            ),
            session_needs_reconcile=needs_reconcile,
            live_resource_count=live_resource_count,
            remaining_resource_kinds=tuple(sorted(kinds)),
            errors=tuple(errors),
        )

    @staticmethod
    async def _stop_component(name: str, component: Any) -> str | None:
        """停止一个调度器，并把异常压缩为不含配置和正文的诊断。"""

        if component is None:
            return None
        try:
            await component.stop()
        except BaseException as exc:  # noqa: BLE001 - 其他组件仍须继续收敛。
            return f"{name}:{type(exc).__name__}"
        return None

    async def _close_codex_manager(self) -> str | None:
        """关闭共享 Codex manager，并返回可安全公开的错误类型。"""

        manager = self._codex_manager
        if manager is None:
            return None
        try:
            await manager.close()
        except BaseException as exc:  # noqa: BLE001 - 报告交给 Host 升级处理。
            return f"codex_manager:{type(exc).__name__}"
        return None
