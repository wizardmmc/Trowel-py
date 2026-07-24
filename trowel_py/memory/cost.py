"""daily review 的客观 session 成本提取，不判断 pain。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionCost:
    """仅包含客观信号；pain 由提炼 agent 作语义判断。"""

    total_tokens: int
    num_turns: int
    error_count: int


def extract_session_cost(
    usage: dict[str, Any] | None, num_turns: int, error_count: int
) -> SessionCost:
    usage = usage or {}
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    return SessionCost(
        total_tokens=inp + out,
        num_turns=int(num_turns or 0),
        error_count=int(error_count or 0),
    )


def extract_cost_from_jsonl(jsonl_path: str | Path) -> SessionCost:
    """按 CC 2.1.197 真实持久 JSONL 的计量语义提取客观成本。

    持久文件没有 live stdout 的 ``result`` 或 ``system/init`` 行。input 与 cache
    input 是累积值，只取最后一个 assistant；output 是逐轮增量，需要求和。
    assistant 行数仅作为 turn 代理，error 来自 user 内的
    ``tool_result.is_error``。文件不可读时返回全零，由 agent 从 transcript 判断。
    """
    last_input = 0
    total_output = 0
    assistant_count = 0
    error_count = 0
    try:
        with open(str(jsonl_path), encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or not s.startswith("{"):
                    continue
                try:
                    ev = json.loads(s)
                except json.JSONDecodeError:
                    continue
                et = ev.get("type")
                if et == "assistant":
                    assistant_count += 1
                    u = (ev.get("message") or {}).get("usage") or {}
                    last_input = (
                        int(u.get("input_tokens") or 0)
                        + int(u.get("cache_read_input_tokens") or 0)
                        + int(u.get("cache_creation_input_tokens") or 0)
                    )
                    total_output += int(u.get("output_tokens") or 0)
                elif et == "user":
                    content = (ev.get("message") or {}).get("content")
                    if isinstance(content, list):
                        for block in content:
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_result"
                                and block.get("is_error")
                            ):
                                error_count += 1
    except OSError:
        return SessionCost(0, 0, 0)
    return SessionCost(
        total_tokens=last_input + total_output,
        num_turns=assistant_count,
        error_count=error_count,
    )
