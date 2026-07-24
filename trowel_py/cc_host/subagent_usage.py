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
    """按录制确认的 task 身份关系构造 transcript 路径。"""
    return (
        cc_projects_root()
        / workdir_to_slug(workdir)
        / cc_session_id
        / "subagents"
        / f"agent-{task_id}.jsonl"
    )


def sum_transcript_usage(path: Path) -> dict[str, int] | None:
    """累加 assistant usage 与 tool_use；文件缺失或不可读时返回 None。"""
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
    """用 transcript 总量覆盖 CC 空计量，保留 duration_ms 等其余字段。"""
    merged: dict[str, Any] = dict(cc_usage) if isinstance(cc_usage, dict) else {}
    merged["total_tokens"] = summed["total_tokens"]
    merged["tool_uses"] = summed["tool_uses"]
    return merged
