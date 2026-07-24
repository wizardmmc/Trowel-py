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

# 交互工具由 control_request 渲染，不能同时生成普通工具块。
_ELICIT_TOOL_NAMES = frozenset({"AskUserQuestion"})


def _is_elicit_tool(name: str) -> bool:
    return name in _ELICIT_TOOL_NAMES


logger = logging.getLogger(__name__)


class Translator:
    def __init__(self) -> None:
        # 每次发送创建一个实例；累加器与去重集合不得跨轮复用。
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
        top_type = cc_event.get("type")
        handler = self._dispatch.get(top_type)
        if handler is None:
            return []
        # handler 异常必须向上冒泡，由调用链在流式边界转换为终态错误事件。
        return handler(cc_event)

    def _on_system(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        return translate_system_event(ev, as_text_fn=_as_text, logger=logger)

    def _on_stream_event(self, ev: dict[str, Any]) -> list[TrowelEvent]:
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
        # 必须先闭合 block 取得完整调用，再做跨来源去重与交互工具分流。
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
        # envelope 可能是唯一内容源；usage 先发出，tool_use 再按 ID 与流式结果去重。
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
        # 顶层 tool_use_result 的预计算 diff 必须随每个工具结果传给展示层。
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
        return [
            ToolProgressEvent(
                tool_use_id=ev.get("tool_use_id", ""),
                tool_name=ev.get("tool_name", ""),
                elapsed_time_seconds=float(ev.get("elapsed_time_seconds", 0.0)),
            )
        ]

    def _on_result(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        # 每个 result 分支都先重置累加器；后台任务场景会在同一逻辑轮内继续下一个原生片段。
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
        req = ev.get("request") or {}
        if req.get("subtype") != "can_use_tool":
            return []
        if req.get("tool_name") != "AskUserQuestion":
            return []
        tool_use_id = req.get("tool_use_id")
        request_id = ev.get("request_id")
        if not tool_use_id or not request_id:
            # 缺少关联 ID 时必须告警并丢弃，不能发出无法回传的半成品事件。
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
