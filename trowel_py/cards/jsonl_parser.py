"""
parse claude code jsonl conversation logs into structured messages
"""
import json
import logging
from pydantic import BaseModel, ValidationError
from typing import Literal, Any

logger = logging.getLogger(__name__)

class ChatMessage(BaseModel):
    """
    One turn of a conversation
    """
    role: Literal["user", "assistant"]
    content: str


class JsonlParseError(ValueError):
    """
    Raised when the input is fundamentally unusable (e.g. empty file).
    """


def parse_jsonl(text: str) -> list[ChatMessage]:
    """
    Parse a JSONL conversation log into a flat list of user/assistant messages

    Args:
        text: raw file content, one JSON object per line

    Returns:
        user/assistant messages in original order

    Raises:
        JsonlParseError: if the input is empty
    """
    if not text or not text.strip():
        raise JsonlParseError("empty input: nothing to parse")
    
    messages: list[ChatMessage] = []
    skipped = 0

    for lineno, raw_line in enumerate(text.splitlines(), start=1):  # avoid big file
        line = raw_line.strip()
        if not line:
            continue    # blank line between records, not an error

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
                lineno, extracted.get("role")
            )
            skipped += 1
            continue

    if skipped:
        logger.info("jsonl parsed: %d messages kept, %d lines skipped", len(messages), skipped)
    return messages


def _extract_message(obj: Any) -> dict[str, str] | None:
    """
    extract a normalized {role, content: str} from one parsed JSON line

    Args:
        obj: the parsed JSON value of one line

    Returns:
        {"role":..., "content":...} or None
    """
    source = obj.get("message") if isinstance(obj, dict) and isinstance(obj.get("message"), dict) else obj
    if not isinstance(source, dict):
        return None
    role = source.get("role")
    text = _content_to_text(source.get("content"))
    if not role or not text:
        return None
    return {
        "role": role,
        "content": text
    }


def _content_to_text(content: Any) -> str:
    """
    normalize CC content (str or list of blocks) into plain text

    Args:
        content: the raw content field (str or list)

    Returns:
        the flattened text, or ""
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