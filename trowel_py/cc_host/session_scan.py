"""List resumable CC history sessions for a workdir.

CC persists each session as a JSONL file under ~/.claude/projects/<slug>/
where <slug> is the workdir path with '/' replaced by '-'. We scan that dir
and return summaries of the sessions cc's own ``--resume`` picker would show
— most-recent-first, with cc's title priority (customTitle > aiTitle >
lastPrompt > firstPrompt).

Filtering aligns with the leaked source's ``parseSessionInfoFromLite``
(claude-code-main/src/utils/listSessionsImpl.ts). A file is a *resumable
session* iff:

1. its stem is a valid UUID (cc writes sessions as ``<uuid>.jsonl``);
2. its **first line** does NOT carry ``isSidechain:true`` — that marks a
   sub-agent (Task tool) session's own jsonl, which cc ``--resume`` hides;
3. it yields a summary (customTitle / aiTitle / lastPrompt / firstPrompt) —
   metadata-only files (queue-operation / attachment, no real prompt) are
   hidden too.

This is NOT limited to sessions trowel created — any CC session the user ran
from that workdir (including months-old ones) shows up, mirroring cc.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Read this many bytes from the head and the tail of each session file. cc
# writes the first user message near the top and titles (aiTitle/customTitle)
# near the end, so a head+tail slice is enough without loading a multi-MB
# transcript. Mirrors readSessionLite's approach in the leaked source.
_HEAD_BYTES = 8192
_TAIL_BYTES = 8192

# Match ``"isSidechain": true`` (with optional whitespace) on a session's first
# line — that line marks a sub-agent session's own jsonl.
_SIDECHAIN_FIRST_LINE = re.compile(r'"isSidechain"\s*:\s*true')


@dataclass(frozen=True)
class SessionSummary:
    """One resumable CC history session."""

    cc_session_id: str
    title: str
    updated_at: float  # epoch seconds (file mtime)


def cc_projects_root() -> Path:
    """~/.claude/projects (overridable in tests)."""
    return Path.home() / ".claude" / "projects"


def workdir_to_slug(workdir: str | os.PathLike) -> str:
    """Workdir path → CC's projects-dir slug ('/' → '-')."""
    return str(workdir).replace("/", "-")


def _is_valid_uuid_session_id(stem: str) -> bool:
    """True if the filename stem is a valid UUID (cc's session id shape)."""
    try:
        uuid.UUID(stem)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def count_sessions(workdir: str | os.PathLike) -> int:
    """Count resumable CC sessions for workdir (after cc --resume filtering).

    Same filtering rules as :func:`list_sessions` — the raw glob count is wrong
    (it includes sub-agent and metadata-only files cc hides). Implemented as
    ``len(list_sessions(...))`` so the total can never drift from the list.
    """
    return len(list_sessions(workdir))


def list_sessions(
    workdir: str | os.PathLike, *, limit: int | None = None
) -> list[SessionSummary]:
    """Return resumable CC sessions for workdir, most-recent-first.

    Args:
        workdir: the workdir whose CC project slug is scanned.
        limit: if set, cap the result to the N most recent sessions. None
            (default) returns all. The history dropdown uses this so it
            doesn't surface hundreds of months-old sessions at once.

    Returns:
        filtered + sorted session summaries (cc ``--resume``'s view).
    """
    slug = workdir_to_slug(workdir)
    proj_dir = cc_projects_root() / slug
    if not proj_dir.is_dir():
        return []
    out: list[SessionSummary] = []
    for f in proj_dir.glob("*.jsonl"):
        if not _is_valid_uuid_session_id(f.stem):
            continue
        try:
            mtime = f.stat().st_mtime
            title = _extract_title(f)
        except OSError:
            # file vanished between glob and read (CC rotates its own logs)
            continue
        except Exception as exc:  # noqa: BLE001 — one malformed jsonl must not
            # crash the whole dropdown (a bad bytes slice, regex on weird input,
            # etc.). Skip the file, keep the rest of the list working.
            logger.debug("skipping unparseable session file %s: %s", f, exc)
            continue
        if title == "":
            # metadata-only (no customTitle/aiTitle/lastPrompt/firstPrompt)
            continue
        out.append(
            SessionSummary(
                cc_session_id=f.stem,
                title=title,
                updated_at=mtime,
            )
        )
    out.sort(key=lambda s: s.updated_at, reverse=True)
    if limit is not None:
        return out[:limit]
    return out


def _read_head_tail(path: Path) -> tuple[str, str]:
    """Return (head_text, tail_text) — first/last bytes of the file, utf-8.

    Kept bounded so a multi-MB transcript costs at most 2× _HEAD/_TAIL bytes
    of read. Used for title extraction + first-line sidechain check.
    """
    size = path.stat().st_size
    head_text = ""
    tail_text = ""
    with path.open("rb") as fh:
        if size > 0:
            head_bytes = min(_HEAD_BYTES, size)
            fh.seek(0)
            head_text = fh.read(head_bytes).decode("utf-8", errors="replace")
        if size > _HEAD_BYTES:
            fh.seek(size - _TAIL_BYTES)
            tail_text = fh.read().decode("utf-8", errors="replace")
        elif size > 0:
            # small file: tail == head (already read); avoid double work
            tail_text = head_text
    return head_text, tail_text


def _extract_title(path: Path) -> str:
    """Pick a display title for a session file, cc --resume style.

    Priority (ported from parseSessionInfoFromLite): ``customTitle`` >
    ``aiTitle`` > ``lastPrompt`` > first user message text. Returns "" when
    nothing is extractable (caller treats that as "metadata-only, hide").

    Also returns "" when the file's first line marks it a sub-agent session
    (``isSidechain:true``) — cc hides those, and so do we.
    """
    head, tail = _read_head_tail(path)

    # First-line sidechain check: cc writes the marker on the very first line.
    first_line = head.split("\n", 1)[0]
    if _SIDECHAIN_FIRST_LINE.search(first_line):
        return ""

    custom = _last_string_field(tail, "customTitle") or _last_string_field(head, "customTitle")
    if custom:
        return custom
    ai = _last_string_field(tail, "aiTitle") or _last_string_field(head, "aiTitle")
    if ai:
        return ai
    last_prompt = _last_string_field(tail, "lastPrompt")
    if last_prompt:
        return last_prompt
    return _first_user_text_from_head(head)


def _last_string_field(blob: str, field: str) -> str:
    """Last ``"field":"value"`` occurrence in blob (cc's inline jsonl fields).

    Scans the text directly (no full JSON parse) the way the leaked source's
    ``extractLastJsonStringField`` does, so it works on a head/tail byte slice.
    Returns "" if not found.
    """
    pattern = re.compile(r'"' + re.escape(field) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"')
    matches = pattern.findall(blob)
    if not matches:
        return ""
    raw = matches[-1]
    try:
        # the captured group is a JSON string body — re-decode escapes safely
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw


def _first_user_text_from_head(head: str) -> str:
    """Scan the head slice for the first real user text message.

    Mirrors the old ``_first_user_text`` but operates on an already-read head
    blob. Skips ``tool_result`` echoes and ``last-prompt`` metadata entries
    (cc's extractFirstPromptFromHead filters those too).
    """
    for raw in head.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "user":
            continue
        content = ev.get("message", {}).get("content")
        # A user message may actually be a tool_result echo (cc wraps tool
        # results in a type:"user" envelope) — skip those so we don't grab
        # tool output as the title. Mirrors cc's extractFirstPromptFromHead.
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            continue
        text = _extract_text(content)
        if text:
            return text
    return ""


def _extract_text(content: object) -> str:
    """Pull the first text string out of a CC message content block list.

    Args:
        content: a string, or a list of content blocks (dicts with type/text).

    Returns:
        the first block's text, or "" if none is a text block.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return ""
