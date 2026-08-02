"""协调运行时登记、binding 持久化和关闭的原子生命周期。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from trowel_py.agent_host.binding import Runtime, SessionBinding
from trowel_py.agent_host.capacity import SessionCapacityGate
from trowel_py.agent_host.delegate_identity import NonUserIdentityStore
from trowel_py.agent_host.runtimes.base import RuntimeCloseResult, RuntimeSessionPort
from trowel_py.agent_host.store import BindingStore
from trowel_py.resource_lifecycle.models import OwnerScope
from trowel_py.resource_lifecycle.registry import ResourceRegistry


class SessionInFlightError(Exception):
    """表示保守清理要求会话无活动轮次，但实时状态仍未终结。"""


class SessionReconcileRequiredError(Exception):
    """表示 runtime 尚有临时资源，binding 必须保留供后续重试。"""


@dataclass(frozen=True)
class SessionCloseResult:
    """公开一个 Trowel 会话关闭后的可重试结果。

    Attributes:
        status: `closed` 表示临时资源归零且 binding 已按请求处理；
            `needs_reconcile` 表示仍需重试；`not_found` 表示会话本来就不存在。
        remaining_resource_count: 尚未确认关闭的临时资源数量。
        remaining_resource_kinds: 尚未关闭的资源类型，按名称排序且去重。
        error: 去敏后的失败说明；成功或会话不存在时为 None。
    """

    status: Literal["closed", "needs_reconcile", "not_found"]
    remaining_resource_count: int = 0
    remaining_resource_kinds: tuple[str, ...] = ()
    error: str | None = None

    @classmethod
    def not_found(cls) -> SessionCloseResult:
        """构造幂等关闭中“原本不存在”的结果。"""

        return cls(status="not_found")

    @classmethod
    def from_runtime(cls, result: RuntimeCloseResult) -> SessionCloseResult:
        """把 runtime 资源核验结果转换为会话边界结果。"""

        return cls(
            status=result.status,
            remaining_resource_count=result.remaining_resource_count,
            remaining_resource_kinds=result.remaining_resource_kinds,
            error=result.error,
        )


class SessionLifecycle:
    """让 runtime 登记、持久化事实和容量状态一起提交或回滚。"""

    def __init__(
        self,
        store: BindingStore,
        non_user_identities: NonUserIdentityStore,
        runtime_ports: Mapping[Runtime, RuntimeSessionPort],
        capacity: SessionCapacityGate,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        """绑定生命周期需要的事实源、统一容量门和可选资源账本。

        Args:
            store: 保存 Trowel 会话 binding 的持久仓储。
            non_user_identities: 长期排除所有非用户会话的原生身份索引。
            runtime_ports: 两种 runtime 到实时状态和关闭操作的映射。
            capacity: 原子提交会话连接、在跑和关闭状态的统一容量门。
            resource_registry: 当前应用实例的临时资源账本；None 时仅依赖 runtime
                自身的关闭结果。
        """

        self._store = store
        self._non_user_identities = non_user_identities
        self._runtime_ports = dict(runtime_ports)
        self._capacity = capacity
        self._resource_registry = resource_registry

    def migrate_non_user_identities(self) -> None:
        """把升级前仍保留 binding 的非用户原生 ID 写入长期索引。"""

        for binding in self._store.list_all():
            self.remember_non_user_identity(binding)

    def remember_non_user_identity(
        self,
        binding: SessionBinding,
        native_session_id: str | None = None,
    ) -> None:
        """在 binding 已确认不是用户会话且已有原生 ID 时持久登记。"""

        effective_native_id = native_session_id or binding.native_session_id
        if (
            binding.session_kind == "user"
            or not isinstance(effective_native_id, str)
            or not effective_native_id
        ):
            return
        self._non_user_identities.add(binding.runtime, effective_native_id)

    def commit_created(self, binding: SessionBinding) -> None:
        """提交新 binding；任一步失败都撤销 binding 和 runtime 登记。"""

        stored = False
        try:
            self._store.put(binding)
            stored = True
            self.remember_non_user_identity(binding)
        except BaseException as exc:
            if stored:
                try:
                    self._store.delete(binding.session_id)
                except BaseException as rollback_error:
                    exc.add_note(
                        "failed to roll back binding "
                        f"{binding.session_id}: {rollback_error!r}"
                    )
            self.abort_created(binding.runtime, binding.session_id, exc)
            raise

    def abort_created(
        self,
        runtime: Runtime,
        session_id: str,
        primary_error: BaseException,
    ) -> None:
        """撤销尚未提交完成的 runtime 登记，同时保留原始异常。"""

        port = self._runtime_ports.get(runtime)
        if port is None:
            primary_error.add_note(
                f"failed to roll back runtime {runtime.value}: adapter unavailable"
            )
            return
        try:
            port.abort_create(session_id)
        except BaseException as rollback_error:
            primary_error.add_note(
                f"failed to roll back runtime {runtime.value} session "
                f"{session_id}: {rollback_error!r}"
            )

    async def close(
        self,
        binding: SessionBinding,
        *,
        require_idle: bool,
        busy_message: str,
        before_runtime_close: Callable[[], None] | None = None,
        before_binding_delete: Callable[[], None] | None = None,
        delete_binding: bool = True,
    ) -> RuntimeCloseResult:
        """阻止新轮次后关闭 runtime，并在必需写入完成后删除 binding。

        Args:
            binding: 要关闭的 Trowel 会话绑定。
            require_idle: 是否在 runtime 仍有未结束轮次时拒绝关闭。
            busy_message: 拒绝关闭活动会话时返回的说明。
            before_runtime_close: 关闭 runtime 前执行的同步清理。
            before_binding_delete: runtime 已关闭后、binding 删除前必须成功完成的
                同步写入；失败时保留 binding 供调用方重试。
            delete_binding: 是否删除 binding；应用退出时为 False，以保留恢复入口。

        Returns:
            runtime 与资源账本共同确认的关闭结果。
        """

        self.remember_non_user_identity(binding)
        token = self._capacity.begin_close(binding.session_id)
        try:
            if self._resource_registry is not None:
                self._resource_registry.mark_owner_closing(
                    OwnerScope.SESSION,
                    agent_session_id=binding.session_id,
                )
            if require_idle and self._capacity.has_in_flight_turn(binding):
                raise SessionInFlightError(busy_message)
            if before_runtime_close is not None:
                before_runtime_close()
            runtime = self._runtime_ports[binding.runtime]
            result = await runtime.close(binding)
            if not result.is_closed:
                self._capacity.cancel_close(binding.session_id, token)
                return result
            if self._resource_registry is not None:
                summary = self._resource_registry.owner_summary(
                    OwnerScope.SESSION,
                    agent_session_id=binding.session_id,
                )
                if summary.live_resource_count:
                    self._capacity.cancel_close(binding.session_id, token)
                    return RuntimeCloseResult.needs_reconcile(
                        remaining_resource_count=summary.live_resource_count,
                        remaining_resource_kinds=summary.remaining_resource_kinds,
                        error="session resources still need reconciliation",
                    )
            if before_binding_delete is not None:
                before_binding_delete()
            self._capacity.complete_close(
                binding.session_id,
                token,
                delete_binding=delete_binding,
            )
            return RuntimeCloseResult.closed()
        except BaseException:
            self._capacity.cancel_close(binding.session_id, token)
            raise
