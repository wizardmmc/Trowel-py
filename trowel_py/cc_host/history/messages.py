"""CC 历史消息的清洗与事件翻译。"""

from __future__ import annotations

from collections.abc import Callable
from re import Pattern
from typing import Any

from trowel_py.cc_host.schemas import TrowelEvent


def clean_user_text(
    text: str,
    *,
    command_name_re: Pattern[str],
    command_args_re: Pattern[str],
    skill_trigger_re: Pattern[str],
) -> str:
    """恢复真实输入，并丢弃 CC 持久化的内部注入。"""
    name_match = command_name_re.search(text)
    if name_match:
        name = name_match.group(1).strip()
        args_match = command_args_re.search(text)
        args = args_match.group(1).strip() if args_match else ""
        return f"/{name} {args}" if args else f"/{name}"
    if "<local-command-stdout>" in text:
        return ""
    if text.lstrip().startswith("<task-notification>"):
        return ""
    trigger_match = skill_trigger_re.match(text)
    if trigger_match:
        name = trigger_match.group(1).strip()
        args = trigger_match.group(2).strip()
        return f"/{name} {args}" if args else f"/{name}"
    if text.lstrip().startswith(("<system-reminder>", "<cparam>")):
        return ""
    return text


def translate_user(
    event: dict[str, Any],
    *,
    clean_user_text: Callable[[str], str],
    write_diff_from_result: Callable[[Any], Any],
    user_event_type: Callable[..., TrowelEvent],
    tool_result_event_type: Callable[..., TrowelEvent],
) -> list[TrowelEvent]:
    """从一条历史 user 记录中提取用户输入或工具结果。

    列表内容含有可展示文本时，文本块会合并为一个用户事件；否则返回其中的工具
    结果。多个工具结果共享一个 ``toolUseResult`` 时不附加 write diff，避免把同一
    份文件变化归给多个调用。

    Args:
        event: Claude Code 历史中的 user 记录。
        clean_user_text: 清洗单段 user 文本的函数。
        write_diff_from_result: 从 ``toolUseResult`` 提取文件变化的函数。
        user_event_type: 根据 ``text`` 构造用户事件的类型或函数。
        tool_result_event_type: 根据工具 ID、输出和文件变化构造结果事件的类型或
            函数。

    Returns:
        由该记录生成的用户事件或工具结果；meta 记录、内部输入和不支持的内容
        生成空列表。
    """

    if event.get("isMeta"):
        return []
    content = event.get("message", {}).get("content")
    if isinstance(content, str):
        cleaned = clean_user_text(content)
        return [user_event_type(text=cleaned)] if cleaned else []
    if not isinstance(content, list):
        return []

    write_diff = write_diff_from_result(event.get("toolUseResult"))
    tool_result_count = sum(
        1
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result"
    )
    if tool_result_count > 1:
        write_diff = None

    text_parts: list[str] = []
    translated: list[TrowelEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_result":
            translated.append(
                tool_result_event_type(
                    tool_use_id=str(block.get("tool_use_id", "")),
                    content=str(block.get("content", "")),
                    write_diff=write_diff,
                )
            )
        elif kind == "text":
            cleaned = clean_user_text(str(block.get("text", "")))
            if cleaned:
                text_parts.append(cleaned)
    if text_parts:
        return [user_event_type(text="\n".join(text_parts))]
    return translated


def translate_assistant(
    event: dict[str, Any],
    prev_ts: str | None,
    *,
    compute_thinking_duration: Callable[[Any, Any], int | None],
    text_event_type: Callable[..., TrowelEvent],
    thinking_event_type: Callable[..., TrowelEvent],
    elicitation_event_type: Callable[..., TrowelEvent],
    tool_call_event_type: Callable[..., TrowelEvent],
) -> list[TrowelEvent]:
    """从一条历史 assistant 记录中按内容块生成展示事件。

    ``AskUserQuestion`` 工具调用会转换为交互请求。历史 JSONL 不包含对应的
    ``control_request``，因此交互请求的 ``request_id`` 为空字符串。

    Args:
        event: Claude Code 历史中的 assistant 记录。
        prev_ts: 此前最近一条带时间戳记录的 ISO 时间；没有时为 None。
        compute_thinking_duration: 接收 ``prev_ts`` 和当前记录的 ``timestamp``，
            返回估算思考秒数的函数。
        text_event_type: 根据 ``text`` 构造文本事件的类型或函数。
        thinking_event_type: 根据文本和估算时长构造思考事件的类型或函数。
        elicitation_event_type: 根据工具 ID 和问题列表构造交互请求的类型或函数。
        tool_call_event_type: 根据工具 ID、名称和参数构造普通工具调用的类型或函数。

    Returns:
        按内容块顺序生成的文本、思考、交互请求和工具调用事件；消息内容不是列表
        时为空列表。
    """

    content = event.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    cur_ts = event.get("timestamp")
    translated: list[TrowelEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            translated.append(text_event_type(text=str(block.get("text", ""))))
        elif kind == "thinking":
            translated.append(
                thinking_event_type(
                    text=str(block.get("thinking", "")),
                    thinking_duration_seconds=compute_thinking_duration(
                        prev_ts,
                        cur_ts,
                    ),
                )
            )
        elif kind == "tool_use":
            tool_name = str(block.get("name", ""))
            if tool_name == "AskUserQuestion":
                translated.append(
                    elicitation_event_type(
                        tool_use_id=str(block.get("id", "")),
                        request_id="",
                        questions=list(
                            (block.get("input") or {}).get("questions") or []
                        ),
                    )
                )
            else:
                translated.append(
                    tool_call_event_type(
                        tool_use_id=str(block.get("id", "")),
                        tool_name=tool_name,
                        input=dict(block.get("input", {}) or {}),
                    )
                )
    return translated
