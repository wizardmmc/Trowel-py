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
from datetime import datetime
from typing import Any

from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug
from trowel_py.cc_host.tool_use_result import write_diff_from_cc_result
from trowel_py.schemas.cc_host import (
    ElicitationRequestEvent,
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


def _parse_iso_ts(ts: Any) -> datetime | None:
    """Parse a CC jsonl top-level ``timestamp`` into a tz-aware datetime.

    Args:
        ts: the raw timestamp value (usually an ISO 8601 string, sometimes
            missing/None on malformed entries).

    Returns:
        The datetime, or None if ``ts`` is absent or not a parseable ISO string.
        Never raises — history replay is best-effort.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        # CC writes UTC with a trailing "Z". fromisoformat on 3.11+ accepts it,
        # but normalizing to "+00:00" keeps older interpreters honest too.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compute_thinking_duration(
    prev_ts: Any, thinking_ts: Any
) -> int | None:
    """Reconstruct "Thought for Ns" for a replayed thinking block.

    The live stream times thinking via thinking_tokens heartbeats; the jsonl has
    none, but every entry carries an ISO timestamp and CC persists a thinking
    block as its own assistant entry. So the delta between the thinking entry's
    timestamp and the previous entry's timestamp approximates how long the think
    took — matching what CC's own TUI shows on reload.

    Args:
        prev_ts: the previous jsonl entry's timestamp (raw, may be None).
        thinking_ts: the thinking entry's timestamp (raw, may be None).

    Returns:
        Whole seconds clamped to >=1, or None when there is no prev, no current
        timestamp, the delta is non-positive (clock skew / same instant), or
        either timestamp is unparseable. None makes the frontend fall back to a
        bare "思考" label, consistent with the live path's no-heartbeat branch.
    """
    start = _parse_iso_ts(prev_ts)
    end = _parse_iso_ts(thinking_ts)
    if start is None or end is None:
        return None
    delta = round((end - start).total_seconds())
    if delta <= 0:
        return None
    # Match the live reducer's clamp (ccReducer.ts: Math.max(1, round(...))) so
    # a sub-second think still surfaces as "Thought for 1s", never 0.
    return max(1, delta)


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
    # Timestamp of the previous successfully-parsed entry, used to reconstruct
    # per-thinking durations on replay (see _compute_thinking_duration). Entries
    # without a timestamp do not update this, so a malformed line in between
    # can't reset the anchor.
    prev_ts: str | None = None
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
            cur_ts = ev.get("timestamp") if isinstance(ev, dict) else None
            events.extend(_translate_line(ev, prev_ts))
            if isinstance(cur_ts, str) and cur_ts:
                prev_ts = cur_ts
    return events


def _translate_line(ev: dict[str, Any], prev_ts: str | None) -> list[TrowelEvent]:
    """Map one jsonl entry to zero or more trowel events.

    Args:
        ev: one parsed jsonl entry (top-level `type` selects the shape).
        prev_ts: the previous entry's raw timestamp, used only to stamp a
            thinking block's reconstructed duration. Unused for non-assistant
            and non-thinking entries.

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
        return _translate_assistant(ev, prev_ts)
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
    # slice-033 feat 2 (方案 F): cc persists the diff it computed at execution
    # time in this jsonl row's top-level `toolUseResult` field. One user row
    # carries one tool_result + its toolUseResult — convert once and attach to
    # the tool_result event so the FE renders real file line numbers on replay.
    write_diff = write_diff_from_cc_result(ev.get("toolUseResult"))
    # One user row = one tool_result + its toolUseResult (cc's current jsonl
    # format). If a future cc ever packs multiple tool_result blocks into one
    # user row, the single toolUseResult can't map to each block — drop it
    # rather than mislabel all but the first.
    if sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_result") > 1:
        write_diff = None
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
                    write_diff=write_diff,
                )
            )
        elif kind == "text":
            text_parts.append(str(block.get("text", "")))
    # Real user turns arrive as text blocks; tool_result echoes as tool_result
    # blocks — the two don't mix in one message.
    if text_parts:
        return [UserEvent(text="\n".join(text_parts))]
    return out


def _translate_assistant(
    ev: dict[str, Any], prev_ts: str | None
) -> list[TrowelEvent]:
    """Map a CC `assistant` entry: each content block -> its trowel event."""
    content = ev.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    cur_ts = ev.get("timestamp")
    out: list[TrowelEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append(TextEvent(text=str(block.get("text", ""))))
        elif kind == "thinking":
            # Note: if an assistant entry carries multiple thinking blocks, they
            # all share the same prev_ts -> cur_ts delta (CC writes one timestamp
            # per top-level entry). This is an inherent jsonl limitation; the
            # approximation is acceptable since multi-thinking entries are rare.
            out.append(
                ThinkingEvent(
                    text=str(block.get("thinking", "")),
                    thinking_duration_seconds=_compute_thinking_duration(
                        prev_ts, cur_ts
                    ),
                )
            )
        elif kind == "tool_use":
            tool_name = str(block.get("name", ""))
            if tool_name == "AskUserQuestion":
                # slice-025-c: replay an AskUserQuestion turn the same way the
                # live stream would — emit elicit_request, then the matching
                # tool_result (translated by _translate_user) flips it to
                # "answered" via the reducer. request_id is not in the jsonl
                # (control_request never persists); the reducer only matches on
                # tool_use_id, so an empty request_id is harmless here.
                out.append(
                    ElicitationRequestEvent(
                        tool_use_id=str(block.get("id", "")),
                        request_id="",
                        questions=list(
                            (block.get("input") or {}).get("questions") or []
                        ),
                    )
                )
            else:
                out.append(
                    ToolCallEvent(
                        tool_use_id=str(block.get("id", "")),
                        tool_name=tool_name,
                        input=dict(block.get("input", {}) or {}),
                    )
                )
    return out
