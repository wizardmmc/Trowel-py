"""验证应用退出协调器的顺序、幂等和资源报告。"""

from __future__ import annotations

import asyncio

from trowel_py.agent_host.lifecycle import SessionCloseResult
from trowel_py.resource_lifecycle import DrainCoordinator, ResourceRegistry


class FakeHub:
    """记录会话收敛调用并返回一个成功结果。"""

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls = 0

    async def close_all(self) -> dict[str, SessionCloseResult]:
        self.calls += 1
        self.order.append("sessions")
        await asyncio.sleep(0)
        return {"private-session": SessionCloseResult(status="closed")}


class FakeComponent:
    """记录调度器或 manager 的关闭顺序。"""

    def __init__(self, order: list[str], name: str) -> None:
        self.order = order
        self.name = name
        self.calls = 0

    async def stop(self) -> None:
        self.calls += 1
        self.order.append(self.name)

    async def close(self) -> None:
        self.calls += 1
        self.order.append(self.name)


async def test_drain_is_idempotent_and_closes_sessions_before_manager() -> None:
    order: list[str] = []
    hub = FakeHub(order)
    scheduler = FakeComponent(order, "scheduler")
    manager = FakeComponent(order, "manager")
    coordinator = DrainCoordinator(
        resource_registry=ResourceRegistry(app_instance_id="test-instance"),
        agent_hub=hub,
        schedulers=(("scheduler", scheduler),),
        codex_manager=manager,
    )

    first, second = await asyncio.gather(coordinator.drain(), coordinator.drain())

    assert first is second
    assert first.status == "closed"
    assert first.session_closed == 1
    assert hub.calls == scheduler.calls == manager.calls == 1
    assert order[0] == "sessions"
    assert order[-1] == "manager"
