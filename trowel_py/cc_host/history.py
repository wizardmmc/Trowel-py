"""Translate a stored CC session jsonl into trowel events for history replay.

The live stream never needs a "user message" event (the frontend appends the
user's own message optimistically). But when replaying a past session, user
text must surface, and reusing the same reducer is a hard spec constraint.
So this translator emits a UserEvent for each historical user turn; it never
runs on the live path.

This is a best-effort, offline translation: CC's persisted jsonl carries
completed content blocks (not deltas), so we map each block 1:1. Fields we
don't recognize are skipped, never fatal.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug
from trowel_py.schemas.cc_host import (
    FinishedEvent,
    SessionStartedEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    TrowelEvent,
    UserEvent,
)

logger = logging.getLogger(__name__)


def _is_safe_session_id(cc_session_id: str) -> bool:
    """True if cc_session_id is a plain filename (no traversal).

    Args:
        cc_session_id: the untrusted id (may come from a public request).

    Returns:
        True if it is non-empty, has no path separators, and is not "." / "..".
    """
    if not cc_session_id or cc_session_id in (".", ".."):
        return False
    if "/" in cc_session_id or "\\" in cc_session_id:
        return False
    return True


def parse_history(workdir: str, cc_session_id: str) -> list[TrowelEvent]:
    """Replay a CC session jsonl as trowel events.

    Args:
        workdir: the working directory the session ran in (determines the
            projects-dir slug).
        cc_session_id: the CC session id (= jsonl filename stem).

    Returns:
        trowel events in chronological order, isomorphic to what the live
        stream would have produced (plus UserEvent for each user turn).
        Empty list if the session file is absent or unreadable.
    """
    slug = workdir_to_slug(workdir)
    # cc_session_id is untrusted (it can flow from a public POST resume_from).
    # Reject anything that is not a plain filename — no path separators, no
    # dot/dotdot — so it can't traverse out of the projects/<slug>/ dir.
    if not _is_safe_session_id(cc_session_id):
        return []
    path = cc_projects_root() / slug / f"{cc_session_id}.jsonl"
    if not path.is_file():
        return []

    events: list[TrowelEvent] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("skipping unparseable line in %s", path)
                continue
            events.extend(_translate_line(ev))
    return events


def _translate_line(ev: dict[str, Any]) -> list[TrowelEvent]:
    """Map one jsonl entry to zero or more trowel events.

    Args:
        ev: one parsed jsonl entry (top-level `type` selects the shape).

    Returns:
        trowel events for this entry (often one, sometimes several for an
        assistant message with multiple content blocks).
    """
    top = ev.get("type")
    if top == "system" and ev.get("subtype") == "init":
        return [
            SessionStartedEvent(
                model=str(ev.get("model", "")),
                cwd=str(ev.get("cwd", "")),
                cc_session_id=str(ev.get("session_id", "")),
                tools=list(ev.get("tools", [])),
            )
        ]
    if top == "user":
        return _translate_user(ev)
    if top == "assistant":
        return _translate_assistant(ev)
    if top == "result" and ev.get("subtype") == "success":
        return [
            FinishedEvent(
                usage=dict(ev.get("usage", {}) or {}),
                total_cost_usd=float(ev.get("total_cost_usd", 0.0) or 0.0),
                num_turns=int(ev.get("num_turns", 0) or 0),
            )
        ]
    return []


def _translate_user(ev: dict[str, Any]) -> list[TrowelEvent]:
    """Map a CC `user` entry: real user turn -> UserEvent, tool_result echo ->
    ToolResultEvent.

    A real user turn arrives as either a plain string OR (the common CC jsonl
    shape) a list of `text` blocks. A tool_result echo arrives as a list of
    `tool_result` blocks and must NOT become a UserEvent.
    """
    content = ev.get("message", {}).get("content")
    if isinstance(content, str):
        return [UserEvent(text=content)]
    if not isinstance(content, list):
        return []
    text_parts: list[str] = []
    out: list[TrowelEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_result":
            out.append(
                ToolResultEvent(
                    tool_use_id=str(block.get("tool_use_id", "")),
                    content=str(block.get("content", "")),
                )
            )
        elif kind == "text":
            text_parts.append(str(block.get("text", "")))
    # Real user turns arrive as text blocks; tool_result echoes as tool_result
    # blocks — the two don't mix in one message.
    if text_parts:
        return [UserEvent(text="\n".join(text_parts))]
    return out


def _translate_assistant(ev: dict[str, Any]) -> list[TrowelEvent]:
    """Map a CC `assistant` entry: each content block -> its trowel event."""
    content = ev.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    out: list[TrowelEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append(TextEvent(text=str(block.get("text", ""))))
        elif kind == "thinking":
            out.append(ThinkingEvent(text=str(block.get("thinking", ""))))
        elif kind == "tool_use":
            out.append(
                ToolCallEvent(
                    tool_use_id=str(block.get("id", "")),
                    tool_name=str(block.get("name", "")),
                    input=dict(block.get("input", {}) or {}),
                )
            )
    return out
