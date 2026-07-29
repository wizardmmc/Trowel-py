"""从 CC subagent transcript 汇总 token 与 tool 使用量。

真实录制和上游累加器表明，GLM 下 task 事件的 usage 可能为空，可信计量来自
assistant 消息的 ``message.usage``。录制同时确认 ``task_id == agentId``，对应
文件位于会话目录的 ``subagents/agent-<task_id>.jsonl``。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug

logger = logging.getLogger(__name__)


def subagent_transcript_path(workdir: str, cc_session_id: str, task_id: str) -> Path:
    """按 `task_id == agentId` 的录制结果构造子代理 transcript 路径。

    Args:
        workdir: 主 CC 会话使用的工作目录。
        cc_session_id: 主会话的原生 CC 会话 ID。
        task_id: task 事件报告的 ID，也是子代理文件名中的 agent ID。

    Returns:
        `<project>/<cc_session_id>/subagents/agent-<task_id>.jsonl` 路径。
    """
    return (
        cc_projects_root()
        / workdir_to_slug(workdir)
        / cc_session_id
        / "subagents"
        / f"agent-{task_id}.jsonl"
    )


def sum_transcript_usage(path: Path) -> dict[str, int] | None:
    """逐行累加子代理 transcript 中的 token 和 `tool_use` 块数量。

    每个可解析字典的 `message.usage.input_tokens` 与 `output_tokens` 都计入 token
    总量，`message.content` 中每个 `tool_use` 块计为一次工具调用。空行、无效 JSON
    和不含字典 `message` 的行跳过，不做跨行去重。

    Args:
        path: 要读取的子代理 JSONL transcript。

    Returns:
        `total_tokens` 与 `tool_uses` 汇总；空文件返回两个 0，文件缺失或读取失败
        时返回 `None`。
    """
    if not path.is_file():
        return None
    total_tokens = 0
    tool_uses = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    total_tokens += _as_int(usage.get("input_tokens"))
                    total_tokens += _as_int(usage.get("output_tokens"))
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_uses += 1
    except OSError as exc:
        logger.debug("subagent transcript unreadable (%s): %s", path, exc)
        return None
    return {"total_tokens": total_tokens, "tool_uses": tool_uses}


def _as_int(value: object) -> int:
    """将计量值转换为整数。

    布尔值返回 0，整数原样返回，浮点数直接交给 `int()` 截断。其他值先转为
    字符串再解析，字符串解析失败时返回 0。

    Args:
        value: transcript usage 字段中的原始计量值。

    Returns:
        转换后的整数；布尔值或字符串解析失败时返回 0。

    Raises:
        ValueError: 浮点数是 `NaN`。
        OverflowError: 浮点数是正无穷或负无穷。
    """

    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def merge_usage(
    cc_usage: dict[str, Any] | None, summed: dict[str, int]
) -> dict[str, Any]:
    """用 transcript 汇总覆盖 CC 的 token 和工具调用量，并保留其他字段。

    Args:
        cc_usage: task 事件原有的 usage；`None` 或非字典值按空字典处理。
        summed: transcript 汇总出的 `total_tokens` 和 `tool_uses`。

    Returns:
        CC 原有字段的副本，其中 `total_tokens` 和 `tool_uses` 无条件替换为汇总值。
    """
    merged: dict[str, Any] = dict(cc_usage) if isinstance(cc_usage, dict) else {}
    merged["total_tokens"] = summed["total_tokens"]
    merged["tool_uses"] = summed["tool_uses"]
    return merged
