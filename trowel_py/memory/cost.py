"""提取 daily review 使用的会话 token 数、轮数和错误数。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionCost:
    """记录会话的 token 数、轮数和错误数，不判断会话是否包含痛点。

    Attributes:
        total_tokens: 输入和输出 token 的合计数；具体算法取决于数据来源。
        num_turns: 会话轮数；从 JSONL 提取时，用 assistant 事件条数作为近似值。
        error_count: 错误次数；从 JSONL 提取时，统计标记为错误的工具结果。
    """

    total_tokens: int
    num_turns: int
    error_count: int


def extract_session_cost(
    usage: dict[str, Any] | None, num_turns: int, error_count: int
) -> SessionCost:
    """把调用方提供的用量、轮数和错误数整理为会话成本。

    Args:
        usage: 包含 ``input_tokens`` 和 ``output_tokens`` 的用量；为 None 或缺少
            字段时，相应 token 数按 0 计算。
        num_turns: 调用方统计的会话轮数。
        error_count: 调用方统计的错误次数。

    Returns:
        包含 token 总数、轮数和错误数的会话成本。
    """
    usage = usage or {}
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    return SessionCost(
        total_tokens=inp + out,
        num_turns=int(num_turns or 0),
        error_count=int(error_count or 0),
    )


def extract_cost_from_jsonl(
    jsonl_path: str | Path,
    *,
    start_offset: int = 0,
    end_offset: int | None = None,
) -> SessionCost:
    """从 journal 的目标字节区间提取近似会话成本。

    Claude Code 2.1.197 的持久文件没有实时 stdout 中的 ``result`` 或
    ``system/init`` 行。本函数取最后一条 assistant 事件的输入与缓存输入 token，
    累加每条 assistant 事件的输出 token，并用 assistant 事件条数近似会话轮数。
    错误数来自 user 事件中的 ``tool_result.is_error``。这些值按持久化事件近似
    计算，不是 Claude Code 提供的权威会话统计。空行、非 JSON 行和无效 JSON 行
    会被跳过；文件不存在或因文件系统错误无法读取时返回全零。offset 按原始
    UTF-8 文件字节计算，调用方应传入完整 JSONL 行边界。

    Args:
        jsonl_path: Claude Code 会话的持久化 JSONL 文件路径。
        start_offset: 本次目标区间的起始字节，默认为文件开头。
        end_offset: 本次目标区间的结束字节；None 表示文件末尾。

    Returns:
        按上述规则计算的 token 总数、轮次数和错误数。

    Raises:
        ValueError: offset 为负数，或结束位置不晚于起始位置。
    """
    if start_offset < 0:
        raise ValueError("cost range start must not be negative")
    if end_offset is not None and end_offset <= start_offset:
        raise ValueError("cost range end must be after start")
    last_input = 0
    total_output = 0
    assistant_count = 0
    error_count = 0
    try:
        with open(str(jsonl_path), "rb") as f:
            f.seek(start_offset)
            while end_offset is None or f.tell() < end_offset:
                remaining = -1 if end_offset is None else end_offset - f.tell()
                raw_line = f.readline(remaining)
                if not raw_line:
                    break
                try:
                    s = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
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
