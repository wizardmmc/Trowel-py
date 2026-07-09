"""Sum token usage from a subagent transcript (slice-036 D 层).

cc under the GLM backend reports ``total_tokens: 0`` in its task_progress /
task_notification system events (the field is empty there), so trowel's
SubagentBlock — which read that field — rendered "0 tokens" and degraded to
hiding the spend line. cc's own TUI shows real numbers anyway because it sums
each assistant message's ``usage.input_tokens + output_tokens`` out of the
subagent's transcript (verified by binary reverse: ``totalUsage``/``cumulative``
→ ``tokenCount`` → ``usage:{total_tokens, tool_uses}``). This module is trowel's
port of that accumulator.

The translation is pure: a transcript path in -> usage dict out. The service
layer backfills it onto SubagentProgressEvent when cc's own usage is empty.

Transcript path (verified against reverse_cc samples/raw/030_task_agenttool):
``<projects-root>/<slug>/<cc_session_id>/subagents/agent-<task_id>.jsonl`` —
``task_id`` (from the task_* events) IS the agentId cc uses as the filename.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug

logger = logging.getLogger(__name__)


def subagent_transcript_path(
    workdir: str, cc_session_id: str, task_id: str
) -> Path:
    """Resolve a Task subagent's transcript file.

    Args:
        workdir: the session workdir (determines the slug).
        cc_session_id: the CC session id (the transcript dir name).
        task_id: the id from cc's task_* events — equal to the agentId cc
            uses as the transcript filename.

    Returns:
        ``<root>/<slug>/<cc_session_id>/subagents/agent-<task_id>.jsonl``.
        The path may not exist yet (subagent still booting); callers handle
        that via sum_transcript_usage returning None.
    """
    return (
        cc_projects_root()
        / workdir_to_slug(workdir)
        / cc_session_id
        / "subagents"
        / f"agent-{task_id}.jsonl"
    )


def sum_transcript_usage(path: Path) -> dict[str, int] | None:
    """Sum token usage across one subagent transcript (slice-036 D 层).

    Walks each assistant row's ``message.usage`` (``input_tokens`` +
    ``output_tokens``) and counts ``tool_use`` blocks. Mirrors cc's own
    ``totalUsage``/``cumulative`` accumulator so the number matches what cc's
    TUI shows under GLM (where cc's event-pushed usage is empty).

    Args:
        path: the ``agent-<task_id>.jsonl`` transcript path.

    Returns:
        ``{total_tokens, tool_uses}`` summed across all assistant rows; or
        None when the file does not exist (subagent still booting / never ran).
        Rows without a usage field, and non-assistant rows (user / tool_result
        echoes), are skipped. A 0/0 thinking envelope contributes 0 — harmless.
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
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_use"
                        ):
                            tool_uses += 1
    except OSError as exc:
        logger.debug("subagent transcript unreadable (%s): %s", path, exc)
        return None
    return {"total_tokens": total_tokens, "tool_uses": tool_uses}


def _as_int(value: object) -> int:
    """Coerce a usage numeric field to int; 0 when absent / not a number."""
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
    """Build the wire usage dict from cc's (possibly empty) usage + the
    transcript sum.

    The transcript sum ALWAYS wins for ``total_tokens`` / ``tool_uses``
    (slice-036 C-4: never rely on ``task_progress.usage`` for tokens under
    GLM — it's empty). cc's ``duration_ms`` is preserved when present (cc does
    report real durations even under GLM, and the transcript has none), so the
    frontend's "(N tool uses · M tokens · Xs)" summary keeps its duration.

    Args:
        cc_usage: cc's own usage dict from the task_* event (may be None or
            carry ``duration_ms`` / a 0 ``total_tokens``). Values may be int
            or float (cc writes ``duration_ms`` as either).
        summed: the transcript accumulator output (``{total_tokens,
            tool_uses}``).

    Returns:
        The merged wire usage dict: cc's fields with the transcript sum
        overriding ``total_tokens`` / ``tool_uses``. Returned as
        ``dict[str, Any]`` because cc's ``duration_ms`` rides along and may
        be a float.
    """
    merged: dict[str, Any] = (
        dict(cc_usage) if isinstance(cc_usage, dict) else {}
    )
    merged["total_tokens"] = summed["total_tokens"]
    merged["tool_uses"] = summed["tool_uses"]
    return merged
