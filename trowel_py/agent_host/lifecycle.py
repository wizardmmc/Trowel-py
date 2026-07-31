"""协调运行时登记、binding 持久化和关闭的原子生命周期。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from trowel_py.agent_host.binding import Runtime, SessionBinding
from trowel_py.agent_host.capacity import SessionCapacityGate
from trowel_py.agent_host.delegate_identity import DelegateIdentityStore
from trowel_py.agent_host.runtimes.base import RuntimeSessionPort
from trowel_py.agent_host.store import BindingStore


class SessionInFlightError(Exception):
    """表示保守清理要求会话无活动轮次，但实时状态仍未终结。"""


class SessionLifecycle:
    """让 runtime 登记、持久化事实和容量状态一起提交或回滚。"""

    def __init__(
        self,
        store: BindingStore,
        delegate_identities: DelegateIdentityStore,
        runtime_ports: Mapping[Runtime, RuntimeSessionPort],
        capacity: SessionCapacityGate,
    ) -> None:
        """绑定生命周期需要的三个事实源和统一容量门。"""

        self._store = store
        self._delegate_identities = delegate_identities
        self._runtime_ports = dict(runtime_ports)
        self._capacity = capacity

    def migrate_delegate_identities(self) -> None:
        """把升级前仍保留 binding 的委派原生 ID 写入长期索引。"""

        for binding in self._store.list_all():
            self.remember_delegate_identity(binding)

    def remember_delegate_identity(
        self,
        binding: SessionBinding,
        native_session_id: str | None = None,
    ) -> None:
        """在 binding 已确认是委派会话且已有原生 ID 时持久登记。"""

        effective_native_id = native_session_id or binding.native_session_id
        if (
            binding.session_kind != "delegate"
            or not isinstance(effective_native_id, str)
            or not effective_native_id
        ):
            return
        self._delegate_identities.add(binding.runtime, effective_native_id)

    def commit_created(self, binding: SessionBinding) -> None:
        """提交新 binding；任一步失败都撤销 binding 和 runtime 登记。"""

        stored = False
        try:
            self._store.put(binding)
            stored = True
            self.remember_delegate_identity(binding)
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
    ) -> None:
        """阻止新轮次后关闭 runtime，并在必需写入完成后删除 binding。

        Args:
            binding: 要关闭的 Trowel 会话绑定。
            require_idle: 是否在 runtime 仍有未结束轮次时拒绝关闭。
            busy_message: 拒绝关闭活动会话时返回的说明。
            before_runtime_close: 关闭 runtime 前执行的同步清理。
            before_binding_delete: runtime 已关闭后、binding 删除前必须成功完成的
                同步写入；失败时保留 binding 供调用方重试。
        """

        self.remember_delegate_identity(binding)
        token = self._capacity.begin_close(binding.session_id)
        try:
            if require_idle and self._capacity.has_in_flight_turn(binding):
                raise SessionInFlightError(busy_message)
            if before_runtime_close is not None:
                before_runtime_close()
            runtime = self._runtime_ports[binding.runtime]
            await runtime.close(binding.session_id)
            if before_binding_delete is not None:
                before_binding_delete()
            self._capacity.complete_close(binding.session_id, token)
        except BaseException:
            self._capacity.cancel_close(binding.session_id, token)
            raise
