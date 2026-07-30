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

    with gate.admit_connection("user"):
        pass


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
