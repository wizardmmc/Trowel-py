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
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug
from trowel_py.cc_host.tool_use_result import write_diff_from_cc_result
from trowel_py.cc_host.workflow_watcher import parse_workflow_tree
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
    WorkflowTreeEvent,
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


def _ts_delta_seconds(prev_ts: Any, cur_ts: Any) -> int | None:
    """Whole seconds between two raw CC jsonl timestamps, clamped to >=1.

    Shared backbone of the history-replay duration reconstructions: the jsonl
    carries no timing signal of its own, but every entry has an ISO timestamp,
    so a delta between two entries approximates a duration (a think, a whole
    turn). Used by both ``_compute_thinking_duration`` and the per-turn
    ``UserEvent.duration_seconds`` back-fill.

    Args:
        prev_ts: the earlier entry's raw timestamp (may be None / unparseable).
        cur_ts: the later entry's raw timestamp (may be None / unparseable).

    Returns:
        Whole seconds clamped to >=1, or None when either timestamp is missing
        or unparseable, or the delta is non-positive (clock skew / same
        instant). None makes the frontend omit the duration label.
    """
    start = _parse_iso_ts(prev_ts)
    end = _parse_iso_ts(cur_ts)
    if start is None or end is None:
        return None
    delta = round((end - start).total_seconds())
    if delta <= 0:
        return None
    # Match the live reducer's clamp (ccReducer.ts: Math.max(1, round(...))) so
    # a sub-second span still surfaces as "1s", never 0.
    return max(1, delta)


def _compute_thinking_duration(
    prev_ts: Any, thinking_ts: Any
) -> int | None:
    """Reconstruct "Thought for Ns" for a replayed thinking block.

    The live stream times thinking via thinking_tokens heartbeats; the jsonl has
    none, but every entry carries an ISO timestamp and CC persists a thinking
    block as its own assistant entry. So the delta between the thinking entry's
    timestamp and the previous entry's timestamp approximates how long the think
    took — matching what CC's own TUI shows on reload. Returns None when no
    usable timestamps are available (frontend falls back to a bare "思考" label,
    consistent with the live path's no-heartbeat branch).
    """
    return _ts_delta_seconds(prev_ts, thinking_ts)


def _close_pending_turn(
    events: list[TrowelEvent], pending: dict[str, Any] | None
) -> None:
    """Back-fill ``duration_seconds`` onto the pending turn's UserEvent.

    Called when the NEXT user turn starts, or at EOF. The pending turn's
    duration is the delta from its user-entry timestamp to the last timestamp
    seen in the turn (its final assistant entry). Replaces the UserEvent at
    ``pending["user_idx"]`` with an immutable ``model_copy`` carrying the
    duration; a no-op when ``pending`` is None (no open turn) or when the delta
    is unusable (``_ts_delta_seconds`` returns None → frontend omits the label).
    """
    if pending is None:
        return
    duration = _ts_delta_seconds(pending["user_ts"], pending["last_ts"])
    idx = pending["user_idx"]
    events[idx] = events[idx].model_copy(update={"duration_seconds": duration})


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
    # Pending turn awaiting its duration back-fill. Set when a UserEvent is
    # appended (a real user turn starts); its duration is the delta from the
    # turn's user-entry timestamp to its last seen entry timestamp, back-filled
    # onto the UserEvent when the NEXT turn starts or at EOF. None outside a
    # turn. (jsonl has no `result` line, so this is how history reconstructs the
    # "Ran for Ns" label the live path derives from send→finished.)
    pending: dict[str, Any] | None = None
    # slice-036 (C 层): preload completed-workflow snapshots from the
    # same-named transcript dir's workflows/wf_*.json. cc persists the whole
    # tree there (it pushes nothing to the dialogue jsonl), so replay reads it
    # back to render the workflow tree identically to the live completed state
    # (invariant C-1). Injected after the first Workflow tool_use so the tree
    # lands in the turn that launched it; appended at end if the tool_use isn't
    # in this jsonl (orphan — defensive).
    workflow_snapshots = _load_workflow_snapshots(path.parent / cc_session_id)
    # Inject each snapshot after its matching Workflow tool_use, in order
    # (snapshots are sorted by startTime; tool_uses appear in launch order).
    # Previously ALL snapshots were dumped after the first tool_use, which
    # stacked every workflow onto one turn on reload (slice-036 bug C).
    workflows_injected = 0
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
            for tev in _translate_line(ev, prev_ts):
                if isinstance(tev, UserEvent):
                    # A real user turn starts — close out the previous turn's
                    # duration, then anchor a new pending turn at this UserEvent
                    # (about to be appended at len(events)).
                    _close_pending_turn(events, pending)
                    pending = {
                        "user_idx": len(events),
                        "user_ts": cur_ts,
                        "last_ts": cur_ts,
                    }
                events.append(tev)
                if (
                    workflows_injected < len(workflow_snapshots)
                    and isinstance(tev, ToolCallEvent)
                    and tev.tool_name == "Workflow"
                ):
                    events.append(workflow_snapshots[workflows_injected])
                    workflows_injected += 1
            if isinstance(cur_ts, str) and cur_ts:
                prev_ts = cur_ts
                # Advance the pending turn's "last seen" timestamp so its
                # duration spans user-entry → this turn's final entry. Rebuilt
                # immutably (pending is a local accumulator).
                if pending is not None:
                    pending = {**pending, "last_ts": cur_ts}
    # orphan snapshots (more wf.json than tool_uses — e.g. a workflow whose
    # tool_use lives in a sidechain jsonl tcc didn't parse) still surface so a
    # completed/killed run isn't invisible on reload.
    for i in range(workflows_injected, len(workflow_snapshots)):
        events.append(workflow_snapshots[i])
    # Back-fill the final turn's duration (no next UserEvent to trigger it).
    _close_pending_turn(events, pending)
    return events


def _load_workflow_snapshots(transcript_dir: Path) -> list[WorkflowTreeEvent]:
    """Read workflows/wf_*.json under a session transcript dir (slice-036 C 层).

    cc writes one wf_<runId>.json per workflow run into the same-named dir
    beside the dialogue jsonl. Each carries the full tree cc aggregated
    (phases + per-agent state/tokens/toolCalls), so this is a straight
    parse_workflow_tree — no reduction.

    Args:
        transcript_dir: ``<projects-root>/<slug>/<cc_session_id>`` (the dir,
            not the .jsonl file).

    Returns:
        Parsed snapshots ordered by ``startTime`` ascending (oldest workflow
        first), so multiple workflows in one session render in launch order.
        Empty when the dir or its workflows/ subdir is absent — a session that
        never ran a workflow, or a workflow still booting (no snapshot yet).
    """
    wf_dir = transcript_dir / "workflows"
    if not wf_dir.is_dir():
        return []
    snapshots: list[tuple[int, WorkflowTreeEvent]] = []
    for f in wf_dir.glob("wf_*.json"):
        try:
            wf = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("skipping unreadable workflow snapshot %s", f)
            continue
        if not isinstance(wf, dict):
            continue
        try:
            ev = parse_workflow_tree(wf)
        except Exception as exc:  # noqa: BLE001 — one bad file must not break replay
            logger.warning("workflow snapshot parse failed (%s): %s", f, exc)
            continue
        start_raw = wf.get("startTime")
        start = (
            int(start_raw)
            if isinstance(start_raw, (int, float)) and not isinstance(start_raw, bool)
            else 0
        )
        snapshots.append((start, ev))
    snapshots.sort(key=lambda t: t[0])
    return [ev for _, ev in snapshots]


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
                # slice-042: carry the roster through replay so a resumed
                # session's first /slash-items has a floor before the live
                # init arrives (empty if this transcript's init row lacks it).
                slash_commands=list(ev.get("slash_commands", [])),
                skills=list(ev.get("skills", [])),
                agents=list(ev.get("agents", [])),
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


# --- slice-035 bug4: scrub CC-internal injections from reloaded user rows ------
#
# CC persists several "user" rows that are NOT real user input. The live path
# never echoes them (translator._on_user only extracts tool_result), so history
# must scrub them too — otherwise reload shows a polluted user bubble. Patterns:
_COMMAND_NAME_RE = re.compile(r"<command-name>\s*/?\s*(\S+?)\s*</command-name>")
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
# Trowel's own skill-trigger expansion (input.py:skill_trigger_prompt).
_SKILL_TRIGGER_RE = re.compile(
    r"^Use the Skill tool with skill='([^']+)'\.\s*(.*)$", re.DOTALL
)


def _clean_user_text(text: str) -> str:
    """Scrub one reloaded user-row text block; return "" to drop it.

    Real user input passes through unchanged. CC-internal payloads are
    normalized so reloaded history matches the live view:

    - raw slash-command tags ``<command-name>..</command-name>`` +
      ``<command-args>..</command-args>`` -> restore ``/name args`` (what the
      user typed);
    - the trowel-expanded skill trigger ``Use the Skill tool with skill='X'.`` ->
      restore ``/X args`` (matches the FE optimistic render at send time);
    - ``<local-command-stdout>`` (e.g. /model output) -> dropped (live path
      routes these via RestartSession/LocalCommand, never into the dialogue);
    - residual ``<system-reminder>`` / ``<cparam>`` -> dropped (defensive; the
      isMeta guard on the row normally already removed these).

    Args:
        text: one user-row text block from the jsonl.

    Returns:
        the cleaned text. Empty means "this block is an injection, drop it"
        (caller skips emitting a UserEvent for it).
    """
    # 1. Raw slash-command tags -> /name args
    name_m = _COMMAND_NAME_RE.search(text)
    if name_m:
        name = name_m.group(1).strip()
        args_m = _COMMAND_ARGS_RE.search(text)
        args = args_m.group(1).strip() if args_m else ""
        return f"/{name} {args}" if args else f"/{name}"
    # 2. Local-command stdout -> drop
    if "<local-command-stdout>" in text:
        return ""
    # 2.5. task-notification -> drop (slice-036 bug5). cc persists workflow/
    # subagent completion notices as user rows with isMeta ABSENT (not True),
    # so the isMeta guard above doesn't catch them; without this they render
    # as a polluted user bubble on reload. Live path never echoes them
    # (translator only extracts tool_result from user rows).
    if text.lstrip().startswith("<task-notification>"):
        return ""
    # 3. Trowel skill-trigger expansion -> /name args
    trigger_m = _SKILL_TRIGGER_RE.match(text)
    if trigger_m:
        name, args = trigger_m.group(1).strip(), trigger_m.group(2).strip()
        return f"/{name} {args}" if args else f"/{name}"
    # 4. Residual injection wrappers -> drop (defensive)
    if text.lstrip().startswith(("<system-reminder>", "<cparam>")):
        return ""
    return text


def _translate_user(ev: dict[str, Any]) -> list[TrowelEvent]:
    """Map a CC `user` entry: real user turn -> UserEvent, tool_result echo ->
    ToolResultEvent.

    slice-035 bug4: rows marked ``isMeta=True`` are CC-internal injections
    (skill descriptions, local-command caveats) and are dropped entirely.
    Surviving text blocks are run through :func:`_clean_user_text` so raw
    command tags / skill-trigger expansions / local-command stdout don't
    leak into the reloaded user bubble.
    """
    # slice-035 bug4: isMeta rows are never real user input — drop wholesale.
    if ev.get("isMeta"):
        return []
    content = ev.get("message", {}).get("content")
    if isinstance(content, str):
        cleaned = _clean_user_text(content)
        return [UserEvent(text=cleaned)] if cleaned else []
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
            cleaned = _clean_user_text(str(block.get("text", "")))
            if cleaned:
                text_parts.append(cleaned)
    # Real user turns arrive as (cleaned) text blocks; tool_result echoes as
    # tool_result blocks — the two don't surface together in practice.
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
