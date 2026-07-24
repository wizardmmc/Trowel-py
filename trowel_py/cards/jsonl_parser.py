"""将 Claude Code JSONL 对话记录解析为结构化消息。"""

import json
import logging
from pydantic import BaseModel, ValidationError
from typing import Literal, Any

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class JsonlParseError(ValueError):
    """仅表示输入整体不可用；单行错误由解析器跳过。"""


def parse_jsonl(text: str) -> list[ChatMessage]:
    """空白输入抛出 ``JsonlParseError``，其余无法提取的行直接跳过。"""
    if not text or not text.strip():
        raise JsonlParseError("empty input: nothing to parse")

    messages: list[ChatMessage] = []
    skipped = 0

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skip line %d: not valid JSON - %s", lineno, line[:80])
            skipped += 1
            continue

        extracted = _extract_message(obj)
        if extracted is None:
            logger.debug("skip line %d: no usable user/assistant message", lineno)
            skipped += 1
            continue

        try:
            messages.append(ChatMessage.model_validate(extracted))
        except ValidationError:
            logger.debug(
                "skip line %d: role=%r not user/assistant",
                lineno,
                extracted.get("role"),
            )
            skipped += 1
            continue

    if skipped:
        logger.info(
            "jsonl parsed: %d messages kept, %d lines skipped", len(messages), skipped
        )
    return messages


def _extract_message(obj: Any) -> dict[str, str] | None:
    """兼容 ``message`` 包装与裸消息两种记录形状。"""
    source = (
        obj.get("message")
        if isinstance(obj, dict) and isinstance(obj.get("message"), dict)
        else obj
    )
    if not isinstance(source, dict):
        return None
    role = source.get("role")
    text = _content_to_text(source.get("content"))
    if not role or not text:
        return None
    return {"role": role, "content": text}


def _content_to_text(content: Any) -> str:
    """CC 内容块只提取 ``text``，忽略思考与工具块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]
        return "\n".join(parts)
    return ""
