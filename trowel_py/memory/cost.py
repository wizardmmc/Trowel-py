"""objective session-cost extraction for the write loop (slice-040 T5).

The daily review feeds objective cost data (tokens / turns / error count) to
the distillation agent as INPUT for its pain judgment. Per grill §7 / C-5,
Python does NOT compute pain — pain is the agent's semantic judgment (severity
of the mistake + resolution cost). This module only extracts objective
quantities the agent would otherwise miscount by eyeballing a transcript.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionCost:
    """Objective cost signals for one session (NOT pain — C-5).

    Attributes:
        total_tokens: input + output tokens consumed by the session.
        num_turns: cc turn count for the session.
        error_count: number of error events (tool failures / retries proxy).
    """

    total_tokens: int
    num_turns: int
    error_count: int


def extract_session_cost(
    usage: dict[str, Any] | None, num_turns: int, error_count: int
) -> SessionCost:
    """Build a SessionCost from the finished-event signals.

    Args:
        usage: the FinishedEvent.usage dict (``input_tokens`` / ``output_tokens``),
            or None when the event carried no usage.
        num_turns: FinishedEvent.num_turns.
        error_count: number of ErrorEvents observed during the session.

    Returns:
        SessionCost (objective only; there is deliberately no pain field — C-5).
    """
    usage = usage or {}
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    return SessionCost(
        total_tokens=inp + out,
        num_turns=int(num_turns or 0),
        error_count=int(error_count or 0),
    )


def extract_cost_from_jsonl(jsonl_path: str | Path) -> SessionCost:
    """Scan a persisted cc session jsonl for its objective cost.

    cc 2.1.197's persisted jsonl (``~/.claude/projects/<slug>/<id>.jsonl``) has
    NO ``type==result`` rows and NO ``system/init`` rows — those exist only in
    cc's live stdout stream-json (which service.py translates). The persisted
    file is a stream of ``queue-operation`` / ``attachment`` / ``user`` /
    ``assistant`` / ``last-prompt`` / ``mode`` rows. Token usage lives on each
    ``assistant`` row at ``message.usage``.

    Token accounting:
      - cc re-sends the full history each turn, so the LAST assistant row's
        ``input_tokens + cache_read + cache_creation`` ≈ the session's total
        input (cumulative). We take the last, not a sum, to avoid double-count.
      - ``output_tokens`` is per-turn (incremental), so we SUM it across turns.
      - ``num_turns`` = assistant row count (a turn-count proxy; cc persists no
        authoritative num_turns in this file).
      - ``error_count`` = ``tool_result.is_error`` blocks inside ``user`` rows.

    Returns a zero SessionCost if the file is missing/unreadable; the agent
    then judges cost from the transcript itself.
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
