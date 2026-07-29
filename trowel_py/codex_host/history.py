"""将 Codex ``thread/read`` 快照重建为可回放的内部事件。

重建过程只读取快照中的已知字段，并为输出事件重新编号。未知或畸形条目会被跳过，
不会阻断同一线程中其余历史的回放。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host.translator import CodexTranslator

_log = logging.getLogger(__name__)
_TOOL_ITEM_TYPES = frozenset({"commandExecution", "fileChange", "mcpToolCall"})
_ACTIVITY_ITEM_TYPES = frozenset({"subAgentActivity", "collabAgentToolCall"})


def events_from_thread(
    session_id: str,
    thread: Mapping[str, Any],
    *,
    translator: CodexTranslator | None = None,
    include_turn_started: bool = False,
) -> list[CodexEvent]:
    """按快照顺序重建消息、工具活动和轮次终态事件。

    Args:
        session_id: 接收重放事件的 Trowel 会话 ID。
        thread: ``thread/read`` 返回的 Codex thread 对象。
        translator: 用于重建工具、子 Agent 活动和终态事件的 translator；省略时
            创建无状态实例。
        include_turn_started: 是否为每个有 ID 的 turn 合成 ``TURN_STARTED``。合成
            事件标记为自主执行且不参与记忆写入。

    Returns:
        从 1 开始连续编号的历史事件。未知条目、无效字段和无法翻译的单个条目会被
        跳过。
    """

    native_thread_id = thread.get("id")
    thread_id = native_thread_id if isinstance(native_thread_id, str) else None
    native_translator = translator or CodexTranslator()
    events: list[CodexEvent] = []

    def append(
        type_: CodexEventType,
        *,
        turn_id: str | None = None,
        item_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        """按当前事件数分配序号并追加一个历史事件。"""

        events.append(
            CodexEvent(
                session_id=session_id,
                seq=len(events) + 1,
                type=type_,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
                payload=payload or immutable_payload(),
            )
        )

    turns = thread.get("turns")
    if not isinstance(turns, list):
        return events
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        raw_turn_id = turn.get("id")
        turn_id = raw_turn_id if isinstance(raw_turn_id, str) else None
        if include_turn_started and turn_id is not None:
            append(
                CodexEventType.TURN_STARTED,
                turn_id=turn_id,
                payload=immutable_payload(autonomous=True, memory_eligible=False),
            )
        items = turn.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                item_type = item.get("type")
                raw_item_id = item.get("id")
                item_id = raw_item_id if isinstance(raw_item_id, str) else None
                if item_type == "userMessage":
                    text = _user_text(item.get("content"))
                    if text:
                        append(
                            CodexEventType.USER,
                            turn_id=turn_id,
                            item_id=item_id,
                            payload=immutable_payload(text=text),
                        )
                elif item_type == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        # Adapter 会丢弃实时最终消息；历史正文需作为 delta 才能显示。
                        append(
                            CodexEventType.ASSISTANT_DELTA,
                            turn_id=turn_id,
                            item_id=item_id,
                            payload=immutable_payload(delta=text),
                        )
                elif item_type == "reasoning":
                    reasoning = _reasoning_text(item)
                    if reasoning:
                        append(
                            CodexEventType.REASONING_DELTA,
                            turn_id=turn_id,
                            item_id=item_id,
                            payload=immutable_payload(delta=reasoning),
                        )
                elif item_type in _TOOL_ITEM_TYPES and turn_id and thread_id:
                    _append_tool_events(
                        events,
                        session_id=session_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item=item,
                        translator=native_translator,
                    )
                elif item_type in _ACTIVITY_ITEM_TYPES and turn_id and thread_id:
                    _append_activity_event(
                        events,
                        session_id=session_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item=item,
                        translator=native_translator,
                    )
        _append_terminal(
            events,
            session_id=session_id,
            thread_id=thread_id,
            turn=turn,
            translator=native_translator,
        )
    return events


def _user_text(content: Any) -> str:
    """合并用户消息中的非空文本片段。"""

    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"]
        for item in content
        if isinstance(item, Mapping)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
        and item["text"]
    )


def _reasoning_text(item: Mapping[str, Any]) -> str:
    """合并思考条目中的摘要和正文片段。"""

    fragments: list[str] = []
    for field in ("summary", "content"):
        value = item.get(field)
        if isinstance(value, list):
            fragments.extend(part for part in value if isinstance(part, str) and part)
    return "\n".join(fragments)


def _append_tool_events(
    events: list[CodexEvent],
    *,
    session_id: str,
    thread_id: str,
    turn_id: str,
    item: Mapping[str, Any],
    translator: CodexTranslator,
) -> None:
    """从工具快照重建启动和完成事件；协议错误只跳过当前条目。"""

    params = {"threadId": thread_id, "turnId": turn_id, "item": item}
    try:
        translated = translator.translate("item/started", params)
        translated += translator.translate("item/completed", params)
    except ProtocolViolationError:
        _log.debug("skipping malformed Codex history item", exc_info=True)
        return
    for native in translated:
        events.append(_stamp(session_id, len(events) + 1, native))


def _append_activity_event(
    events: list[CodexEvent],
    *,
    session_id: str,
    thread_id: str,
    turn_id: str,
    item: Mapping[str, Any],
    translator: CodexTranslator,
) -> None:
    """按快照状态重建子 Agent 活动事件；协议错误只跳过当前条目。"""

    method = "item/started" if item.get("status") == "inProgress" else "item/completed"
    try:
        translated = translator.translate(
            method, {"threadId": thread_id, "turnId": turn_id, "item": item}
        )
    except ProtocolViolationError:
        _log.debug("skipping malformed Codex history activity", exc_info=True)
        return
    for native in translated:
        events.append(_stamp(session_id, len(events) + 1, native))


def _append_terminal(
    events: list[CodexEvent],
    *,
    session_id: str,
    thread_id: str | None,
    turn: Mapping[str, Any],
    translator: CodexTranslator,
) -> None:
    """为 completed、interrupted 或 failed 轮次追加终态事件。

    缺少 thread ID、尚未结束或无法翻译的轮次不会产生事件。
    """

    if thread_id is None or turn.get("status") not in {
        "completed",
        "interrupted",
        "failed",
    }:
        return
    try:
        translated = translator.translate(
            "turn/completed", {"threadId": thread_id, "turn": turn}
        )
    except ProtocolViolationError:
        _log.debug("skipping malformed Codex history turn terminal", exc_info=True)
        return
    for native in translated:
        events.append(_stamp(session_id, len(events) + 1, native))


def _stamp(session_id: str, seq: int, item: TranslatedItem) -> CodexEvent:
    """补充会话 ID 和序号，原样保留中间事件的 Codex 路由字段与 payload。"""

    return CodexEvent(
        session_id=session_id,
        seq=seq,
        type=item.type,
        thread_id=item.thread_id,
        turn_id=item.turn_id,
        item_id=item.item_id,
        payload=item.payload,
    )
