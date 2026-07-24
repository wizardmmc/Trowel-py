"""跟踪 CC 单轮中尚未结束的后台 task。

CC 2.1.197 的真实录制表明，中间 ``result`` 到达时后台 task 可能仍在运行。
``task_notification`` 以及 TaskOutput 路径的
``task_updated.patch.status=completed`` 是当前已确认的终止信号；shape 校验由
service 负责，本模块只维护 task 身份。Workflow 状态仍由 ``WorkflowWatcher``
维护。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingTask:
    task_id: str
    tool_use_id: str
    task_type: str | None
    last_status: str


class BackgroundActivityTracker:
    """跟踪单轮后台任务；send 与 disconnect drain 只能顺序访问。"""

    def __init__(self) -> None:
        self._pending: dict[str, PendingTask] = {}

    def register_started(
        self, task_id: str, tool_use_id: str, task_type: str | None
    ) -> None:
        if not task_id:
            return
        self._pending[task_id] = PendingTask(
            task_id=task_id,
            tool_use_id=tool_use_id,
            task_type=task_type,
            last_status="started",
        )

    def mark_progress(self, task_id: str) -> None:
        """只更新已登记的 task，不根据 progress 创建身份。"""
        if not task_id:
            return
        cur = self._pending.get(task_id)
        if cur is None:
            return
        self._pending[task_id] = PendingTask(
            task_id=cur.task_id,
            tool_use_id=cur.tool_use_id,
            task_type=cur.task_type,
            last_status="progress",
        )

    def terminate(self, task_id: str) -> bool:
        """幂等移除调用者已确认终止的 task；本层不校验原始事件。"""
        if not task_id:
            return False
        return self._pending.pop(task_id, None) is not None

    def has_pending_tasks(self) -> bool:
        return bool(self._pending)

    def pending_ids(self) -> frozenset[str]:
        return frozenset(self._pending)

    def reset(self) -> None:
        """新 turn 前清空；send 入口先等待旧 drain，stdout 代际不会并发。"""
        self._pending.clear()
