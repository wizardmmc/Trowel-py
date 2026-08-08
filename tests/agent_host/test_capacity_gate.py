from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.capacity import (
    CapacityLimitError,
    CapacityLimits,
    SessionCapacityGate,
)
from trowel_py.agent_host.runtimes.base import RuntimeLiveState
from trowel_py.agent_host.store import BindingStore


@dataclass
class _RuntimePort:
    """保存容量测试需要的会话 ID 和实时状态。"""

    runtime: Runtime
    states: dict[str, RuntimeLiveState]

    def session_ids(self) -> tuple[str, ...]:
        """返回当前运行时登记的会话 ID。"""

        return tuple(self.states)

    def live_state(self, session_id: str) -> RuntimeLiveState:
        """返回指定会话的实时状态。"""

        return self.states.get(session_id, RuntimeLiveState.disconnected())

    async def close(self, session_id: str) -> None:
        """从测试运行时移除一个会话。"""

        self.states.pop(session_id, None)

    def abort_create(self, session_id: str) -> None:
        """撤销测试运行时中的会话登记。"""

        self.states.pop(session_id, None)


def _binding(session_id: str, runtime: Runtime, *, kind: str):
    """构造容量测试使用的最小 binding。"""

    return make_binding(
        session_id=session_id,
        runtime=runtime,
        native_session_id=None,
        workdir="/tmp/project",
        model=None,
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        session_kind=kind,
        capabilities=(),
        name=session_id,
    )


def test_capacity_gate_counts_connections_across_runtime_ports(tmp_path: Path) -> None:
    store = BindingStore(tmp_path / "bindings.json")
    cc = _RuntimePort(
        Runtime.CLAUDE_CODE,
        {"cc-delegate": RuntimeLiveState(True, False)},
    )
    codex = _RuntimePort(Runtime.CODEX, {})
    store.put(_binding("cc-delegate", Runtime.CLAUDE_CODE, kind="delegate"))
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: cc, Runtime.CODEX: codex},
        CapacityLimits(
            user_connections=1,
            delegate_connections=1,
            delegate_running=1,
        ),
    )

    with pytest.raises(
        CapacityLimitError,
        match="当前委派数量已满：连接上限为 1",
    ):
        with gate.admit_connection("delegate"):
            raise AssertionError("capacity rejection must happen before creation")

    with pytest.raises(
        CapacityLimitError,
        match="当前委派数量已满：连接上限为 1",
    ):
        with gate.admit_connection("probe"):
            raise AssertionError("all non-user kinds must share the internal pool")

    with gate.admit_connection("user"):
        pass


def test_discussion_uses_independent_eight_connection_pool(tmp_path: Path) -> None:
    """八位研讨参与者不消耗用户或委派连接池，第九位被独立上限拒绝。"""

    store = BindingStore(tmp_path / "bindings.json")
    states: dict[str, RuntimeLiveState] = {}
    for index in range(8):
        session_id = f"discussion-{index}"
        states[session_id] = RuntimeLiveState(True, False)
        store.put(_binding(session_id, Runtime.CLAUDE_CODE, kind="discussion"))
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: _RuntimePort(Runtime.CLAUDE_CODE, states)},
        CapacityLimits(
            user_connections=1,
            delegate_connections=1,
            delegate_running=1,
            discussion_connections=8,
            discussion_running=5,
        ),
    )

    with pytest.raises(CapacityLimitError, match="研讨参与者数量已满"):
        with gate.admit_connection("discussion"):
            raise AssertionError("第九个 discussion connection 不得进入创建窗口")
    with gate.admit_connection("user"):
        pass
    with gate.admit_connection("delegate"):
        pass


def test_discussion_running_pool_stops_at_five_without_blocking_user(
    tmp_path: Path,
) -> None:
    """discussion 的五个运行预留与用户 in-turn 上限彼此隔离。"""

    store = BindingStore(tmp_path / "bindings.json")
    states: dict[str, RuntimeLiveState] = {}
    discussions = []
    for index in range(6):
        session_id = f"discussion-{index}"
        states[session_id] = RuntimeLiveState(True, False)
        binding = _binding(session_id, Runtime.CLAUDE_CODE, kind="discussion")
        store.put(binding)
        discussions.append(binding)
    user = _binding("user", Runtime.CLAUDE_CODE, kind="user")
    states[user.session_id] = RuntimeLiveState(True, False)
    store.put(user)
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: _RuntimePort(Runtime.CLAUDE_CODE, states)},
        CapacityLimits(
            user_connections=1,
            delegate_connections=1,
            delegate_running=1,
            user_running=1,
            discussion_connections=8,
            discussion_running=5,
        ),
    )
    reservations = [gate.reserve_turn(item) for item in discussions[:5]]

    with pytest.raises(CapacityLimitError, match="研讨运行数量已满"):
        gate.reserve_turn(discussions[5])
    user_reservation = gate.reserve_turn(user)

    gate.release_turn(user_reservation)
    for reservation in reservations:
        gate.release_turn(reservation)


def test_capacity_gate_rejects_runtime_registered_under_the_wrong_key(
    tmp_path: Path,
) -> None:
    store = BindingStore(tmp_path / "bindings.json")
    codex = _RuntimePort(Runtime.CODEX, {})

    with pytest.raises(ValueError, match="runtime port key"):
        SessionCapacityGate(
            store,
            {Runtime.CLAUDE_CODE: codex},
            CapacityLimits(
                user_connections=20,
                delegate_connections=5,
                delegate_running=5,
            ),
        )


def test_capacity_gate_reservation_covers_runtime_start_window(
    tmp_path: Path,
) -> None:
    store = BindingStore(tmp_path / "bindings.json")
    cc = _RuntimePort(
        Runtime.CLAUDE_CODE,
        {
            "first": RuntimeLiveState(True, False),
            "second": RuntimeLiveState(True, False),
        },
    )
    first = _binding("first", Runtime.CLAUDE_CODE, kind="delegate")
    second = _binding("second", Runtime.CLAUDE_CODE, kind="delegate")
    store.put(first)
    store.put(second)
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: cc},
        CapacityLimits(
            user_connections=20,
            delegate_connections=5,
            delegate_running=1,
        ),
    )

    reservation = gate.reserve_turn(first)
    with pytest.raises(
        CapacityLimitError,
        match="当前委派数量已满：同时在跑上限为 1",
    ):
        gate.reserve_turn(second)

    gate.release_turn(reservation)
    next_reservation = gate.reserve_turn(second)
    gate.release_turn(next_reservation)


def test_capacity_gate_does_not_double_count_reserved_running_session(
    tmp_path: Path,
) -> None:
    store = BindingStore(tmp_path / "bindings.json")
    cc = _RuntimePort(
        Runtime.CLAUDE_CODE,
        {"first": RuntimeLiveState(True, False)},
    )
    first = _binding("first", Runtime.CLAUDE_CODE, kind="delegate")
    store.put(first)
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: cc},
        CapacityLimits(
            user_connections=20,
            delegate_connections=5,
            delegate_running=1,
        ),
    )

    reservation = gate.reserve_turn(first)
    cc.states["first"] = RuntimeLiveState(True, True)

    assert gate.delegate_running_count() == 1
    gate.release_turn(reservation)


def test_capacity_gate_counts_probe_turn_in_internal_running_pool(
    tmp_path: Path,
) -> None:
    store = BindingStore(tmp_path / "bindings.json")
    cc = _RuntimePort(
        Runtime.CLAUDE_CODE,
        {"probe": RuntimeLiveState(True, False)},
    )
    probe = _binding("probe", Runtime.CLAUDE_CODE, kind="probe")
    store.put(probe)
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: cc},
        CapacityLimits(
            user_connections=20,
            delegate_connections=5,
            delegate_running=1,
        ),
    )

    reservation = gate.reserve_turn(probe)
    assert reservation is not None
    assert gate.delegate_running_count() == 1
    gate.release_turn(reservation)


def test_capacity_gate_atomically_rejects_a_second_user_turn(
    tmp_path: Path,
) -> None:
    """runtime 尚未更新 running 时，用户预留也必须挡住超额启动。"""

    store = BindingStore(tmp_path / "bindings.json")
    cc = _RuntimePort(
        Runtime.CLAUDE_CODE,
        {
            "first": RuntimeLiveState(True, False),
            "second": RuntimeLiveState(True, False),
        },
    )
    first = _binding("first", Runtime.CLAUDE_CODE, kind="user")
    second = _binding("second", Runtime.CLAUDE_CODE, kind="user")
    store.put(first)
    store.put(second)
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: cc},
        CapacityLimits(
            user_connections=20,
            delegate_connections=5,
            delegate_running=5,
            user_running=1,
        ),
    )

    reservation = gate.reserve_turn(first)
    with pytest.raises(CapacityLimitError, match="同时 in-turn"):
        gate.reserve_turn(second)

    gate.release_turn(reservation)
    next_reservation = gate.reserve_turn(second)
    gate.release_turn(next_reservation)


def test_user_and_internal_running_reservations_use_separate_pools(
    tmp_path: Path,
) -> None:
    """用户与内部 turn 的启动预留不能互相消耗独立并发上限。"""

    store = BindingStore(tmp_path / "bindings.json")
    cc = _RuntimePort(
        Runtime.CLAUDE_CODE,
        {
            "user": RuntimeLiveState(True, False),
            "delegate": RuntimeLiveState(True, False),
        },
    )
    user = _binding("user", Runtime.CLAUDE_CODE, kind="user")
    delegate = _binding("delegate", Runtime.CLAUDE_CODE, kind="delegate")
    store.put(user)
    store.put(delegate)
    gate = SessionCapacityGate(
        store,
        {Runtime.CLAUDE_CODE: cc},
        CapacityLimits(
            user_connections=20,
            delegate_connections=5,
            delegate_running=1,
            user_running=1,
        ),
    )

    user_reservation = gate.reserve_turn(user)
    delegate_reservation = gate.reserve_turn(delegate)

    assert gate.user_running_count() == 1
    assert gate.delegate_running_count() == 1
    gate.release_turn(user_reservation)
    gate.release_turn(delegate_reservation)
