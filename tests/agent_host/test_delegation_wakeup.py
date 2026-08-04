"""验证后台委派事件在父会话空闲后自动触发续轮。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from trowel_py.agent_host.delegation_wakeup import DelegationWakeupCoordinator
from trowel_py.agent_host.hub import SessionConflictError


class _ParentTurns:
    """模拟父会话的空闲门禁和自动续轮入口。"""

    def __init__(self) -> None:
        self.idle = asyncio.Event()
        self.idle.set()
        self.started: list[tuple[str, str]] = []
        self.finished = asyncio.Event()
        self.conflicts = 0

    async def wait_until_idle(self, session_id: str) -> None:
        """等待测试放行父会话。"""

        assert session_id == "parent-1"
        await self.idle.wait()

    async def run_automatic_turn(
        self, session_id: str, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        """记录内部通知，并按测试配置模拟一次并发冲突。"""

        if self.conflicts:
            self.conflicts -= 1
            self.idle.clear()
            raise SessionConflictError("parent turn is still running")
        self.started.append((session_id, text))
        yield {"type": "finished", "payload": {}}
        self.finished.set()


def _snapshot(*, status: str, version: int = 2) -> dict[str, Any]:
    """构造与 InteractiveBroker 一致的可执行状态快照。"""

    return {
        "delegation_id": "delegation-1",
        "version": version,
        "parent": {"trowel_session_id": "parent-1"},
        "child": {"runtime": "claude_code", "trowel_session_id": "child-1"},
        "status": status,
        "needs_guidance": {
            "request_id": "request-1",
            "questions": [{"header": "Choice", "question": "A or B?"}],
        }
        if status == "needs_guidance"
        else None,
        "reported": {"answer": "child result"},
        "observed": {"terminal_event": "finished"},
        "error": "child failed" if status == "failed" else None,
    }


@pytest.mark.anyio
async def test_actionable_notice_waits_for_parent_turn_to_finish() -> None:
    """父会话仍在工作时不打断，空闲后才启动内部续轮。"""

    parent = _ParentTurns()
    parent.idle.clear()
    coordinator = DelegationWakeupCoordinator(parent)

    await coordinator.publish(_snapshot(status="needs_guidance"))
    await asyncio.sleep(0)
    assert parent.started == []

    parent.idle.set()
    await asyncio.wait_for(parent.finished.wait(), timeout=0.2)

    assert len(parent.started) == 1
    session_id, prompt = parent.started[0]
    assert session_id == "parent-1"
    assert "delegation-1" in prompt
    assert "needs_guidance" in prompt
    assert "A or B?" in prompt
    assert "delegate_respond" in prompt
    await coordinator.shutdown()


@pytest.mark.anyio
async def test_notice_is_delivered_once_for_same_delegation_version() -> None:
    """重复发布同一状态版本不会消耗第二次父模型调用。"""

    parent = _ParentTurns()
    coordinator = DelegationWakeupCoordinator(parent)
    snapshot = _snapshot(status="completed")

    await coordinator.publish(snapshot)
    await coordinator.publish(snapshot)
    await asyncio.wait_for(parent.finished.wait(), timeout=0.2)
    await asyncio.sleep(0)

    assert len(parent.started) == 1
    assert "child result" in parent.started[0][1]
    await coordinator.shutdown()


@pytest.mark.anyio
async def test_turn_start_conflict_waits_for_next_idle_boundary() -> None:
    """空闲检查后的并发用户消息获胜时，通知留在队列等待下一次空闲。"""

    parent = _ParentTurns()
    parent.conflicts = 1
    coordinator = DelegationWakeupCoordinator(parent)

    await coordinator.publish(_snapshot(status="failed"))
    await asyncio.sleep(0)
    assert parent.started == []

    parent.idle.set()
    await asyncio.wait_for(parent.finished.wait(), timeout=0.2)

    assert len(parent.started) == 1
    assert "child failed" in parent.started[0][1]
    await coordinator.shutdown()


@pytest.mark.anyio
async def test_non_actionable_snapshot_does_not_start_parent_turn() -> None:
    """starting 和 running 只更新状态，不唤醒父模型。"""

    parent = _ParentTurns()
    coordinator = DelegationWakeupCoordinator(parent)

    await coordinator.publish(_snapshot(status="running", version=1))
    await asyncio.sleep(0)

    assert parent.started == []
    await coordinator.shutdown()


@pytest.mark.anyio
async def test_closed_delegation_releases_its_deduplication_keys() -> None:
    """句柄关闭后不在应用生命周期内永久保留历史版本键。"""

    parent = _ParentTurns()
    coordinator = DelegationWakeupCoordinator(parent)
    snapshot = _snapshot(status="completed")

    await coordinator.publish(snapshot)
    await asyncio.wait_for(parent.finished.wait(), timeout=0.2)
    await coordinator.forget_delegation("delegation-1")
    parent.finished.clear()
    await coordinator.publish(snapshot)
    await asyncio.wait_for(parent.finished.wait(), timeout=0.2)

    assert len(parent.started) == 2
    await coordinator.shutdown()
