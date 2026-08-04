"""把后台委派的可执行状态排队，并在父会话空闲后启动续轮。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from trowel_py.agent_host.hub import SessionConflictError, SessionHubError

_ACTIONABLE_STATES = frozenset({"needs_guidance", "completed", "failed"})
_TURN_IN_PROGRESS = "turn_in_progress"

logger = logging.getLogger(__name__)


class ParentTurnPort(Protocol):
    """规定委派通知等待和启动父会话续轮所需的最小接口。"""

    async def wait_until_idle(self, session_id: str) -> None:
        """等到指定父会话没有未结束的原生 turn。"""

        ...

    def run_automatic_turn(
        self, session_id: str, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        """用内部通知启动父会话续轮，并持续返回统一事件。"""

        ...


@dataclass(frozen=True)
class DelegationNotice:
    """保存一次需要父模型处理的委派状态版本。

    Attributes:
        delegation_id: Agent Host 为委派分配的稳定 ID。
        version: 委派状态的单调递增版本，用于阻止重复唤醒。
        parent_session_id: 接收内部通知的父 Trowel 会话 ID。
        status: 本次状态，只允许 needs_guidance、completed 或 failed。
        snapshot: broker 在该版本生成的完整状态快照。
    """

    delegation_id: str
    version: int
    parent_session_id: str
    status: str
    snapshot: Mapping[str, Any]

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> DelegationNotice | None:
        """把 broker 快照转换为可投递通知，非可执行状态返回 None。

        Args:
            snapshot: InteractiveBroker 生成的当前状态快照。

        Returns:
            字段完整的可执行通知；starting、running 等状态返回 None。

        Raises:
            ValueError: 可执行快照缺少稳定委派 ID、版本或父会话 ID。
        """

        status = snapshot.get("status")
        if status not in _ACTIONABLE_STATES:
            return None
        delegation_id = snapshot.get("delegation_id")
        version = snapshot.get("version")
        parent = snapshot.get("parent")
        parent_session_id = (
            parent.get("trowel_session_id") if isinstance(parent, Mapping) else None
        )
        if not isinstance(delegation_id, str) or not delegation_id:
            raise ValueError("actionable delegation snapshot has no delegation_id")
        if not isinstance(version, int) or version < 1:
            raise ValueError("actionable delegation snapshot has no valid version")
        if not isinstance(parent_session_id, str) or not parent_session_id:
            raise ValueError("actionable delegation snapshot has no parent session")
        return cls(
            delegation_id=delegation_id,
            version=version,
            parent_session_id=parent_session_id,
            status=str(status),
            snapshot=dict(snapshot),
        )

    @property
    def key(self) -> tuple[str, int]:
        """返回用于同一应用生命周期内去重的委派版本键。"""

        return (self.delegation_id, self.version)

    def render_prompt(self) -> str:
        """生成只供父模型处理、不要求再次查询状态的内部通知。"""

        payload = json.dumps(
            self.snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (
            "<trowel-delegation-event>\n"
            f"{payload}\n"
            "</trowel-delegation-event>\n\n"
            "这是 Agent Host 投递的后台委派状态，不是用户新发的任务。立即处理这个"
            "版本，不要为它调用 delegate_status。状态为 needs_guidance 时，先依据"
            "现有上下文回答；能确定答案就调用 trowel_agents.delegate_respond，必须"
            "由用户决定时再向用户提问。状态为 completed 时，使用 reported.answer "
            "继续原任务。状态为 failed 时，说明失败并决定是否需要补救。句柄不再需要"
            "时调用 trowel_agents.delegate_close。"
        )


class DelegationWakeupCoordinator:
    """按父会话串行投递委派通知，并避免同一版本重复启动模型。"""

    def __init__(self, parent_turns: ParentTurnPort) -> None:
        """绑定统一父会话 turn 入口，并建立应用级通知队列。

        Args:
            parent_turns: 等待父会话空闲并启动内部续轮的 Session Hub 或测试替身。
        """

        self._parent_turns = parent_turns
        self._queues: dict[str, asyncio.Queue[DelegationNotice]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._accepted: dict[tuple[str, int], str] = {}
        self._closing = False
        self._lock = asyncio.Lock()

    async def publish(self, snapshot: Mapping[str, Any]) -> None:
        """登记一个可执行状态版本，并确保对应父会话有唯一投递任务。

        Args:
            snapshot: InteractiveBroker 在状态变化后生成的完整快照。
        """

        notice = DelegationNotice.from_snapshot(snapshot)
        if notice is None:
            return
        async with self._lock:
            if self._closing or notice.key in self._accepted:
                return
            self._accepted[notice.key] = notice.parent_session_id
            queue = self._queues.setdefault(
                notice.parent_session_id, asyncio.Queue()
            )
            queue.put_nowait(notice)
            worker = self._workers.get(notice.parent_session_id)
            if worker is None or worker.done():
                self._workers[notice.parent_session_id] = asyncio.create_task(
                    self._run_parent(notice.parent_session_id, queue),
                    name=f"delegation-wakeup-{notice.parent_session_id}",
                )

    async def close_parent(self, parent_session_id: str) -> None:
        """停止指定父会话尚未开始的通知投递。

        Args:
            parent_session_id: 正在关闭或删除的父 Trowel 会话 ID。
        """

        async with self._lock:
            worker = self._workers.pop(parent_session_id, None)
            self._queues.pop(parent_session_id, None)
            self._accepted = {
                key: parent
                for key, parent in self._accepted.items()
                if parent != parent_session_id
            }
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def forget_delegation(self, delegation_id: str) -> None:
        """释放已关闭委派留下的版本去重记录。

        Args:
            delegation_id: 已经从 InteractiveBroker 删除的委派 ID。
        """

        async with self._lock:
            self._accepted = {
                key: parent
                for key, parent in self._accepted.items()
                if key[0] != delegation_id
            }

    async def shutdown(self) -> None:
        """停止全部通知任务，供 Agent Host 退出前收敛资源。"""

        async with self._lock:
            self._closing = True
            workers = tuple(self._workers.values())
            self._workers.clear()
            self._queues.clear()
            self._accepted.clear()
        for worker in workers:
            if not worker.done():
                worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def _run_parent(
        self,
        parent_session_id: str,
        queue: asyncio.Queue[DelegationNotice],
    ) -> None:
        """串行处理一个父会话的通知，避免并发启动多个续轮。

        Args:
            parent_session_id: 当前 worker 专属的父会话 ID。
            queue: 当前父会话按到达顺序保存通知的队列。
        """

        while True:
            notice = await queue.get()
            try:
                await self._deliver(parent_session_id, notice)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 单条通知失败不能杀死同父会话后续投递。
                logger.warning(
                    "delegation notice %s version %s could not wake parent %s",
                    notice.delegation_id,
                    notice.version,
                    parent_session_id,
                    exc_info=True,
                )
            finally:
                queue.task_done()

    async def _deliver(
        self,
        parent_session_id: str,
        notice: DelegationNotice,
    ) -> None:
        """在安全空闲边界启动通知；并发用户 turn 获胜时继续等待。

        Args:
            parent_session_id: 要唤醒的父 Trowel 会话 ID。
            notice: 本次需要模型处理的委派状态版本。
        """

        while True:
            await self._parent_turns.wait_until_idle(parent_session_id)
            try:
                conflicted = False
                async for event in self._parent_turns.run_automatic_turn(
                    parent_session_id, notice.render_prompt()
                ):
                    if _is_turn_in_progress(event):
                        conflicted = True
                if not conflicted:
                    return
            except SessionConflictError:
                continue
            except SessionHubError:
                raise


def _is_turn_in_progress(event: Mapping[str, Any]) -> bool:
    """识别 Claude Code 用 error event 表达的并发 turn 冲突。"""

    if event.get("type") != "error":
        return False
    payload = event.get("payload")
    return isinstance(payload, Mapping) and payload.get("subclass") == _TURN_IN_PROGRESS
