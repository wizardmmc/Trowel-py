"""从 participant 根 turn 事件中投影最终公开正文。"""

from __future__ import annotations

from dataclasses import dataclass, field


_WORK_BOUNDARIES = frozenset(
    {
        "thinking",
        "thinking_progress",
        "tool_call",
        "tool_progress",
        "tool_result",
        "elicit_request",
        "approval_request",
        "subagent_progress",
        "subagent_activity",
        "retrying",
        "compact_boundary",
        "compaction",
        "local_command",
        "workflow_tree",
    }
)


@dataclass
class FinalAnswerProjection:
    """保留最后一次工作事件之后连续出现的根级文字。

    纯文字回答的全部文本就是最终答案；模型先解释、再调用工具、最后总结时，
    工具之前的中间文字属于工作轨迹，不能重复进入公开正文。
    """

    _trailing_text: list[str] = field(default_factory=list)

    def observe(self, event_type: object, text: object = None) -> None:
        """消费一条根 turn 事件并更新最终文字候选。

        Args:
            event_type: AgentEvent 的事件类型。
            text: text 事件 payload 中的文字；其他事件省略。
        """

        if event_type == "text":
            if isinstance(text, str):
                self._trailing_text.append(text)
            return
        if event_type in _WORK_BOUNDARIES:
            self._trailing_text.clear()

    def answer(self) -> str:
        """返回去除首尾空白的最终连续文字段。"""

        return "".join(self._trailing_text).strip()
