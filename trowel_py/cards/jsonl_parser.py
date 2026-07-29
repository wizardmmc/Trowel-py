"""将 Claude Code JSONL 对话记录解析为结构化消息。"""

import json
import logging
from pydantic import BaseModel, ValidationError
from typing import Literal, Any

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    """记录从对话中提取的一条用户或助手文本消息。

    Attributes:
        role: 消息发送方，只能是 ``"user"`` 或 ``"assistant"``。
        content: 消息的纯文本；多个文本块之间用换行符连接，不包含思考和工具块。
    """

    role: Literal["user", "assistant"]
    content: str


class JsonlParseError(ValueError):
    """输入为空或只含空白字符时抛出；单行解析错误不会触发此异常。"""


def parse_jsonl(text: str) -> list[ChatMessage]:
    """按原顺序提取 Claude Code JSONL 中的用户和助手文本消息。

    无法解析的 JSON、非消息记录、字段不完整的消息和其他角色会被逐行跳过；
    所有非空行都不可用时返回空列表。

    Args:
        text: 完整的 JSONL 对话记录。

    Returns:
        从有效记录中提取的消息。

    Raises:
        JsonlParseError: 输入为空或只包含空白字符。
    """
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
    """从 ``message`` 包装或裸记录中提取角色和文本，不可用时返回 ``None``。"""
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
    """把 Claude Code 消息内容转成纯文本，不可用时返回空字符串。

    字符串会原样保留；内容块列表只拼接 ``text`` 块，忽略思考和工具块。
    """
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
