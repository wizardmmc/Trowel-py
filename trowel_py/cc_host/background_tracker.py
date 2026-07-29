"""记录当前逻辑轮次中仍在运行的 Claude Code 后台任务。

真实录制表明，Claude Code 可能在后台任务结束前发出中间 ``result``。当前已
确认的完成信号是 ``task_notification``，以及 TaskOutput 路径中的
``task_updated.patch.status=completed``。调用方负责校验事件结构，本模块只按
task ID 维护任务状态；Workflow 状态由 ``WorkflowWatcher`` 单独维护。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingTask:
    """记录一个仍在运行的 Claude Code 后台任务。

    Attributes:
        task_id: Claude Code 分配的后台任务 ID，用于关联后续状态事件。
        tool_use_id: 启动该任务的工具调用 ID。
        task_type: Claude Code 报告的任务类型；未报告时为 None。
        last_status: 最近收到的状态，目前为 ``started`` 或 ``progress``。
    """

    task_id: str
    tool_use_id: str
    task_type: str | None
    last_status: str


class BackgroundActivityTracker:
    """维护当前逻辑轮次中尚未结束的 Claude Code 后台任务。

    同一会话的实时发送循环和断线清理任务共用一个跟踪器，并且只能顺序访问。
    """

    def __init__(self) -> None:
        """创建不含后台任务的跟踪器。"""

        self._pending: dict[str, PendingTask] = {}

    def register_started(
        self, task_id: str, tool_use_id: str, task_type: str | None
    ) -> None:
        """登记已经收到启动信号的后台任务。

        Args:
            task_id: Claude Code 分配的后台任务 ID；空值会被忽略。
            tool_use_id: 启动该任务的工具调用 ID。
            task_type: Claude Code 报告的任务类型；未报告时为 None。
        """

        if not task_id:
            return
        self._pending[task_id] = PendingTask(
            task_id=task_id,
            tool_use_id=tool_use_id,
            task_type=task_type,
            last_status="started",
        )

    def mark_progress(self, task_id: str) -> None:
        """记录已登记的后台任务收到过进度事件。

        未知或为空的 task ID 会被忽略，进度事件不会创建任务记录。

        Args:
            task_id: Claude Code 分配的后台任务 ID。
        """
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
        """移除调用方已确认结束的后台任务。

        本函数不校验原始事件，重复移除或未知 task ID 不会改变状态。

        Args:
            task_id: Claude Code 分配的后台任务 ID。

        Returns:
            找到并移除任务时为 True，否则为 False。
        """
        if not task_id:
            return False
        return self._pending.pop(task_id, None) is not None

    def has_pending_tasks(self) -> bool:
        """判断当前轮次是否还有未结束的后台任务。"""

        return bool(self._pending)

    def pending_ids(self) -> frozenset[str]:
        """返回仍在运行的后台任务 ID。"""

        return frozenset(self._pending)

    def reset(self) -> None:
        """在新逻辑轮次开始前清空全部后台任务。

        调用方必须先等待上一轮的 stdout 清理任务结束，避免清空仍由上一轮更新的
        状态。
        """
        self._pending.clear()
