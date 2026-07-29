"""将 CC stream-json 消息翻译为 Trowel 前端事件。"""

from __future__ import annotations

import logging
from typing import Any

from trowel_py.cc_host.delta import DeltaAccumulator
from trowel_py.cc_host.system_events import translate_system_event
from trowel_py.cc_host.tool_use_result import write_diff_from_cc_result
from trowel_py.cc_host.schemas import (
    ContextUsageEvent,
    ElicitationRequestEvent,
    ErrorEvent,
    FinishedEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    TrowelEvent,
)

_RESULT_ERROR_SUBCLASSES = frozenset(
    {
        "error_during_execution",
        "error_max_turns",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    }
)

# AskUserQuestion 由 control_request 翻译为 elicit_request，不再生成普通 tool_call。
_ELICIT_TOOL_NAMES = frozenset({"AskUserQuestion"})


def _is_elicit_tool(name: str) -> bool:
    """判断工具是否只通过 `control_request` 生成交互事件。"""

    return name in _ELICIT_TOOL_NAMES


logger = logging.getLogger(__name__)


class Translator:
    """翻译一次逻辑发送中的 CC 消息，并维护工具调用的流式状态。

    同一实例覆盖后台任务自动续跑产生的多个原生片段：每个 `result` 清空未闭合的
    工具输入块，但已发布的工具调用 ID 保留到本次逻辑发送结束。下一次发送创建新
    实例，不复用这些状态。
    """

    def __init__(self) -> None:
        """创建空的工具输入累加器、去重集合和顶层消息分发表。"""

        # service 每次逻辑发送创建一个实例，状态不得跨发送复用。
        self._acc = DeltaAccumulator()
        self._emitted_tool_ids: set[str] = set()
        self._dispatch = {
            "system": self._on_system,
            "stream_event": self._on_stream_event,
            "assistant": self._on_assistant,
            "user": self._on_user,
            "tool_progress": self._on_tool_progress,
            "result": self._on_result,
            "control_request": self._on_control_request,
        }

    def translate(self, cc_event: dict[str, Any]) -> list[TrowelEvent]:
        """按 CC 顶层消息类型分发一条 stream-json 消息。

        顶层 `type` 缺失、不是字符串或没有对应处理器时忽略消息。处理器异常不在
        此处捕获，由流式调用边界统一转换为终态错误。

        Args:
            cc_event: CC 输出的一条 stream-json 消息。

        Returns:
            按原消息顺序生成的 Trowel 事件；消息被忽略时返回空列表。
        """

        top_type = cc_event.get("type")
        handler = self._dispatch.get(top_type)
        if handler is None:
            return []
        return handler(cc_event)

    def _on_system(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """把 CC `system` 状态和进度消息交给专用转换器。"""

        return translate_system_event(ev, as_text_fn=_as_text, logger=logger)

    def _on_stream_event(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """按内部事件类型处理内容块的开始、增量和结束。

        块开始只登记元数据，增量由 `_on_delta` 处理，块结束由 `_on_block_stop`
        组装工具调用；其他内部事件不产生输出。
        """

        inner = ev.get("event", {})
        itype = inner.get("type")
        if itype == "content_block_start":
            self._acc.on_block_start(
                inner.get("index", 0), inner.get("content_block", {})
            )
            return []
        if itype == "content_block_delta":
            return self._on_delta(inner)
        if itype == "content_block_stop":
            return self._on_block_stop(inner.get("index", 0))
        return []

    def _on_delta(self, inner: dict[str, Any]) -> list[TrowelEvent]:
        """翻译一个内容块增量。

        文本和思考增量立即生成事件；工具参数分片按块 index 累积，直到块结束才
        生成工具调用。未知增量类型不产生输出。
        """

        delta = inner.get("delta", {})
        dtype = delta.get("type")
        index = inner.get("index", 0)
        if dtype == "text_delta":
            return [TextEvent(text=delta.get("text", ""))]
        if dtype == "thinking_delta":
            return [ThinkingEvent(text=delta.get("thinking", delta.get("text", "")))]
        if dtype == "input_json_delta":
            self._acc.on_input_json_delta(index, delta.get("partial_json", ""))
            return []
        return []

    def _on_block_stop(self, index: int) -> list[TrowelEvent]:
        """在内容块闭合后生成完整且未重复的工具调用。

        Args:
            index: `content_block_stop` 指定的消息内内容块序号。

        Returns:
            单个普通工具调用；块无效、调用 ID 已发布或工具由交互请求处理时返回
            空列表。
        """

        # 先取得完整调用，再与 assistant envelope 按 ID 去重并分流交互工具。
        result = self._acc.on_block_stop(index)
        if result is None:
            return []
        if result.tool_use_id in self._emitted_tool_ids:
            return []
        if _is_elicit_tool(result.tool_name):
            return []
        self._emitted_tool_ids.add(result.tool_use_id)
        return [
            ToolCallEvent(
                tool_use_id=result.tool_use_id,
                tool_name=result.tool_name,
                input=result.input,
            )
        ]

    def _on_assistant(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """按内容顺序拆分完整 assistant envelope。

        `message.usage` 先生成 `ContextUsageEvent`，随后按 `content` 顺序生成文本、
        思考和普通工具调用。assistant envelope 提供完整内容；其中工具调用与已闭合
        的流式块按 ID 去重，文本和思考不做跨来源去重。`AskUserQuestion` 留给
        `control_request`。
        """

        out: list[TrowelEvent] = []
        msg = ev.get("message", {}) or {}
        usage = msg.get("usage")
        if isinstance(usage, dict):
            out.append(
                ContextUsageEvent(
                    message_id=msg.get("id") if isinstance(msg.get("id"), str) else None,
                    model=msg.get("model") if isinstance(msg.get("model"), str) else None,
                    usage=usage,
                )
            )
        for block in msg.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                out.append(TextEvent(text=str(block.get("text", ""))))
            elif kind == "thinking":
                out.append(
                    ThinkingEvent(
                        text=str(block.get("thinking", block.get("text", "")))
                    )
                )
            elif kind == "tool_use":
                tid = block.get("id", "")
                if not tid or tid in self._emitted_tool_ids:
                    continue
                if _is_elicit_tool(block.get("name", "")):
                    continue
                self._emitted_tool_ids.add(tid)
                out.append(
                    ToolCallEvent(
                        tool_use_id=tid,
                        tool_name=block.get("name", ""),
                        input=block.get("input") or {},
                        parent_tool_use_id=ev.get("parent_tool_use_id"),
                    )
                )
        return out

    def _on_user(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """从 user envelope 中提取工具执行结果。

        顶层 `tool_use_result` 的文件 diff 和退出码附到 envelope 内的每个
        `tool_result`。退出码优先读取顶层 `exitCode`；该字段为 `None` 时再读取
        `task.exitCode`，且只接受非布尔整数。`is_error` 同样只接受布尔值。
        """

        # structuredPatch 在顶层 tool_use_result 中，不在 message.content 块内。
        write_diff = write_diff_from_cc_result(ev.get("tool_use_result"))
        out: list[TrowelEvent] = []
        for block in ev.get("message", {}).get("content", []) or []:
            if block.get("type") != "tool_result":
                continue
            out.append(
                ToolResultEvent(
                    tool_use_id=block.get("tool_use_id", ""),
                    content=_as_text(block.get("content")),
                    write_diff=write_diff,
                )
            )
        return out

    def _on_tool_progress(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """把 CC 工具耗时消息转换为单个工具进度事件。

        `elapsed_time_seconds` 缺失时使用 0.0，其他值直接交给 `float()` 转换。
        """

        return [
            ToolProgressEvent(
                tool_use_id=ev.get("tool_use_id", ""),
                tool_name=ev.get("tool_name", ""),
                elapsed_time_seconds=float(ev.get("elapsed_time_seconds", 0.0)),
            )
        ]

    def _on_result(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """把一个原生 `result` 转换为完成或错误事件。

        已知错误 subtype 保留上游错误列表；只有 `subtype == "success"` 且
        `is_error` 为假值时生成完成事件，其余情况生成通用错误事件。所有分支都
        清空尚未闭合的工具输入块，为可能的后台续跑片段隔离分片状态。
        """

        sub = ev.get("subtype")
        if sub in _RESULT_ERROR_SUBCLASSES:
            errors_raw = ev.get("errors") or []
            self._acc.reset()
            return [
                ErrorEvent(
                    subclass=sub,
                    errors=[str(e) for e in errors_raw]
                    if isinstance(errors_raw, list)
                    else [str(errors_raw)],
                    api_error_status=ev.get("api_error_status"),
                )
            ]
        if sub == "success" and not ev.get("is_error"):
            self._acc.reset()
            return [
                FinishedEvent(
                    usage=ev.get("usage") or {},
                    total_cost_usd=float(ev.get("total_cost_usd", 0.0)),
                    num_turns=int(ev.get("num_turns", 0)),
                )
            ]
        self._acc.reset()
        return [
            ErrorEvent(
                subclass=sub or "error", api_error_status=ev.get("api_error_status")
            )
        ]

    def _on_control_request(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """把 `AskUserQuestion` 权限请求转换为可回答的交互事件。

        仅处理 `request.subtype == "can_use_tool"` 的 `AskUserQuestion`。缺少非空
        `tool_use_id` 或 `request_id` 时记录 warning 并丢弃，因为响应无法关联回
        原请求。`questions` 保持上游字段 shape，并以浅拷贝列表传入事件模型。
        """

        req = ev.get("request") or {}
        if req.get("subtype") != "can_use_tool":
            return []
        if req.get("tool_name") != "AskUserQuestion":
            return []
        tool_use_id = req.get("tool_use_id")
        request_id = ev.get("request_id")
        if not tool_use_id or not request_id:
            # 无关联 ID 的事件无法构造 control_response。
            logger.warning(
                "AskUserQuestion control_request missing tool_use_id or "
                "request_id; dropping. raw=%s",
                ev,
            )
            return []
        questions = (req.get("input") or {}).get("questions") or []
        return [
            ElicitationRequestEvent(
                tool_use_id=tool_use_id,
                request_id=request_id,
                questions=list(questions),
            )
        ]


def _as_text(content: Any) -> str:
    """将 CC 消息正文归一化为纯文本。

    `None` 变为空字符串，字符串原样返回。列表只保留字典形式且 `type == "text"`
    的块，并将其 `text` 值按原顺序用换行连接；这些值不做字符串转换。列表以外的
    其他值使用 `str()` 转换。

    Args:
        content: CC `content` 字段的原始值。

    Returns:
        归一化后的文本。

    Raises:
        TypeError: 列表中保留的 `text` 值不是字符串。
    """

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(parts)
    return str(content)
