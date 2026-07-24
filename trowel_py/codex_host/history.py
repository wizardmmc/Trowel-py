"""把公开 ``thread/read`` transcript 转为独立的 Codex history 事件流。"""

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


def events_from_thread(
    session_id: str,
    thread: Mapping[str, Any],
    *,
    translator: CodexTranslator | None = None,
) -> list[CodexEvent]:
    """只转换已知 item；单个未知或漂移 item 不阻断其余 turn。"""

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
                        # live 最终消息会被 adapter 丢弃；静态 history 必须作为 delta 下发。
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
        _append_terminal(
            events,
            session_id=session_id,
            thread_id=thread_id,
            turn=turn,
            translator=native_translator,
        )
    return events


def _user_text(content: Any) -> str:
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
    params = {"threadId": thread_id, "turnId": turn_id, "item": item}
    try:
        translated = translator.translate("item/started", params)
        translated += translator.translate("item/completed", params)
    except ProtocolViolationError:
        _log.debug("skipping malformed Codex history item", exc_info=True)
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
    return CodexEvent(
        session_id=session_id,
        seq=seq,
        type=item.type,
        thread_id=item.thread_id,
        turn_id=item.turn_id,
        item_id=item.item_id,
        payload=item.payload,
    )
