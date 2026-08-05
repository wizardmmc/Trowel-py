"""集中执行跨 Claude Code 与 Codex 的连接和委派轮次容量裁决。"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from trowel_py.agent_capacity import USER_RUNNING_LIMIT
from trowel_py.agent_host.binding import Runtime, SessionBinding, SessionKind
from trowel_py.agent_host.runtimes.base import (
    RuntimeLiveState,
    RuntimeSessionPort,
)
from trowel_py.agent_host.store import BindingStore


@dataclass(frozen=True)
class CapacityLimits:
    """记录用户连接池和委派资源池的进程内上限。

    Attributes:
        user_connections: 用户直接管理的会话最多可登记多少个连接。
        delegate_connections: 两种 runtime 共享的委派子会话连接上限。
        delegate_running: 两种 runtime 共享的委派子会话同时在跑上限。
        user_running: 用户会话同时在跑上限。
    """

    user_connections: int
    delegate_connections: int
    delegate_running: int
    user_running: int = USER_RUNNING_LIMIT


class CapacityLimitError(Exception):
    """表示连接或委派轮次已经达到对应资源池上限。"""


class CapacityConflictError(Exception):
    """表示会话正在关闭，不能再取得新的轮次预留。"""


class SessionCapacityGate:
    """原子裁决跨 runtime 连接创建、轮次启动和 binding 释放。"""

    def __init__(
        self,
        store: BindingStore,
        runtimes: Mapping[Runtime, RuntimeSessionPort],
        limits: CapacityLimits,
    ) -> None:
        """绑定会话事实源、运行时状态入口和固定资源上限。

        Args:
            store: 保存 user/delegate 分类和 runtime 归属的 binding 存储。
            runtimes: 每种 runtime 对应的统一实时状态入口。
            limits: 当前进程内的用户连接和委派资源上限。

        Raises:
            ValueError: mapping 的 runtime key 与 adapter 自报的 runtime 不一致。
        """

        self._store = store
        self._runtimes = dict(runtimes)
        for runtime, port in self._runtimes.items():
            if port.runtime is not runtime:
                raise ValueError(
                    "runtime port key does not match the adapter runtime: "
                    f"{runtime.value} != {port.runtime.value}"
                )
        self._limits = limits
        self._lock = threading.RLock()
        self._turn_reservations: dict[object, str] = {}
        self._close_reservations: dict[str, object] = {}

    @contextmanager
    def admit_connection(self, session_kind: SessionKind) -> Iterator[None]:
        """检查连接池并在创建完成前独占连接变更。

        Args:
            session_kind: 新会话属于用户连接池还是非用户内部连接池。

        Yields:
            连接创建可安全执行的临界区。

        Raises:
            CapacityLimitError: 对应连接池已经达到上限。
        """

        with self._lock:
            count = self.connection_count(session_kind)
            if session_kind != "user":
                if count >= self._limits.delegate_connections:
                    raise CapacityLimitError(
                        "当前委派数量已满："
                        f"连接上限为 {self._limits.delegate_connections}"
                    )
            elif count >= self._limits.user_connections:
                raise CapacityLimitError(
                    "连接数已达上限"
                    f"（{self._limits.user_connections}），请先关闭一些 session"
                )
            yield

    def reserve_turn(self, binding: SessionBinding) -> object:
        """为用户或内部会话原子预留一个同时在跑名额。

        两类会话使用独立上限。预留覆盖 runtime 尚未公开在跑状态的启动窗口，
        调用方必须在启动失败或 runtime 已接管状态后释放。

        Args:
            binding: 即将启动新轮次的会话记录。

        Returns:
            释放预留时使用的内部令牌。

        Raises:
            CapacityLimitError: 对应类别的同时在跑数量已经达到上限。
        """

        with self._lock:
            if binding.session_id in self._close_reservations:
                raise CapacityConflictError(
                    f"session {binding.session_id} 正在关闭，不能启动新轮次"
                )
            if (
                binding.session_kind == "user"
                and self._user_running_count_unlocked() >= self._limits.user_running
            ):
                raise CapacityLimitError(
                    "同时 in-turn 的 session 已达上限"
                    f"（{self._limits.user_running}），等一个完成或中断"
                )
            if (
                binding.session_kind != "user"
                and self._delegate_running_count_unlocked()
                >= self._limits.delegate_running
            ):
                raise CapacityLimitError(
                    "当前委派数量已满："
                    f"同时在跑上限为 {self._limits.delegate_running}"
                )
            token = object()
            self._turn_reservations[token] = binding.session_id
            return token

    def release_turn(self, token: object | None) -> None:
        """释放一次用户或内部轮次启动预留。"""

        if token is None:
            return
        with self._lock:
            self._turn_reservations.pop(token, None)

    def delegate_running_count(self) -> int:
        """返回已经预留或由 runtime 确认仍在处理的非用户轮次数量。"""

        with self._lock:
            return self._delegate_running_count_unlocked()

    def user_running_count(self) -> int:
        """返回已经预留或由 runtime 确认仍在处理的用户轮次数量。"""

        with self._lock:
            return self._user_running_count_unlocked()

    def has_in_flight_turn(self, binding: SessionBinding) -> bool:
        """判断会话是否仍有容量预留或 runtime 未结束轮次。"""

        with self._lock:
            if binding.session_id in self._turn_reservations.values():
                return True
            return self.live_state(binding).has_in_flight_turn

    def begin_close(self, session_id: str) -> object:
        """原子标记会话开始关闭，使后续轮次准入立即失败。"""

        with self._lock:
            if session_id in self._close_reservations:
                raise CapacityConflictError(f"session {session_id} 正在关闭")
            token = object()
            self._close_reservations[session_id] = token
            return token

    def cancel_close(self, session_id: str, token: object) -> None:
        """关闭失败时撤销对应标记，让调用方能够重试。"""

        with self._lock:
            if self._close_reservations.get(session_id) is token:
                self._close_reservations.pop(session_id, None)

    def complete_close(
        self,
        session_id: str,
        token: object,
        *,
        delete_binding: bool = True,
    ) -> None:
        """核对关闭令牌，按需删除 binding，并最终释放标记。

        Args:
            session_id: 正在关闭的 Trowel 会话 ID。
            token: `begin_close` 返回的本次关闭令牌。
            delete_binding: 用户关闭时删除 binding；应用退出时保留以供恢复。
        """

        with self._lock:
            if self._close_reservations.get(session_id) is not token:
                raise CapacityConflictError(
                    f"session {session_id} close reservation is no longer valid"
                )
            try:
                if delete_binding:
                    self._store.delete(session_id)
            finally:
                self._close_reservations.pop(session_id, None)

    def live_state(self, binding: SessionBinding) -> RuntimeLiveState:
        """通过 binding 选择 runtime，并返回会话的统一实时状态。"""

        runtime = self._runtimes.get(binding.runtime)
        if runtime is None:
            return RuntimeLiveState.disconnected()
        return runtime.live_state(binding.session_id)

    def connection_count(self, session_kind: SessionKind) -> int:
        """统计对应类别所属资源池中仍有 binding 的 runtime 登记会话。"""

        with self._lock:
            return sum(
                1
                for runtime in self._runtimes.values()
                for session_id in runtime.session_ids()
                if self._binding_occupies_pool(session_id, session_kind)
            )

    def _delegate_running_count_unlocked(self) -> int:
        """统计非用户运行占用；调用方必须持有容量锁。"""

        reservation_ids = tuple(
            session_id
            for session_id in self._turn_reservations.values()
            if self._binding_is_internal(session_id)
        )
        reserved_sessions = set(reservation_ids)
        runtime_running = sum(
            1
            for binding in self._store.list_all()
            if binding.session_kind != "user"
            and binding.session_id not in reserved_sessions
            and self.live_state(binding).has_in_flight_turn
        )
        return len(reservation_ids) + runtime_running

    def _user_running_count_unlocked(self) -> int:
        """统计用户运行占用；调用方必须持有容量锁。"""

        reservation_ids = tuple(
            session_id
            for session_id in self._turn_reservations.values()
            if self._binding_is_user(session_id)
        )
        reserved_sessions = set(reservation_ids)
        runtime_running = sum(
            1
            for binding in self._store.list_all()
            if binding.session_kind == "user"
            and binding.session_id not in reserved_sessions
            and self.live_state(binding).has_in_flight_turn
        )
        return len(reservation_ids) + runtime_running

    def _binding_is_user(self, session_id: str) -> bool:
        """判断一个容量预留是否属于仍存在的用户 binding。"""

        binding = self._store.get(session_id)
        return binding is not None and binding.session_kind == "user"

    def _binding_is_internal(self, session_id: str) -> bool:
        """判断一个容量预留是否属于仍存在的 delegate 或 probe binding。"""

        binding = self._store.get(session_id)
        return binding is not None and binding.session_kind != "user"

    def _binding_occupies_pool(
        self,
        session_id: str,
        session_kind: SessionKind,
    ) -> bool:
        """判断 runtime 登记项是否占用对应的用户或内部连接池。"""

        binding = self._store.get(session_id)
        return binding is not None and (
            (binding.session_kind == "user") == (session_kind == "user")
        )
