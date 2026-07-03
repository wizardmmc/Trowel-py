"""List resumable CC history sessions for a workdir.

CC persists each session as a JSONL file under ~/.claude/projects/<slug>/
where <slug> is the workdir path with `/` replaced by `-`. We scan that dir,
read the first user message of each file as a title, and return summaries
sorted most-recent-first. This is NOT limited to sessions trowel created — any
CC session the user ran from that workdir (including months-old ones) shows up.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Read at most this many leading lines looking for the first user message,
# so we don't load a multi-MB transcript just to get a title.
_TITLE_SCAN_LINES = 200


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


def count_sessions(workdir: str | os.PathLike) -> int:
    """Count CC session files for workdir without scanning titles.

    Cheap (glob only, no per-file read) — used to show the true total in the
    history dropdown while ``list_sessions`` returns only the most recent N.
    """
    slug = workdir_to_slug(workdir)
    proj_dir = cc_projects_root() / slug
    if not proj_dir.is_dir():
        return 0
    return sum(1 for _ in proj_dir.glob("*.jsonl"))


def list_sessions(
    workdir: str | os.PathLike, *, limit: int | None = None
) -> list[SessionSummary]:
    """Return resumable CC sessions for workdir, most-recent-first.

    Args:
        workdir: the workdir whose CC project slug is scanned.
        limit: if set, cap the result to the N most recent sessions. None
            (default) returns all. The history dropdown uses this so it
            doesn't surface hundreds of months-old sessions at once.
    """
    slug = workdir_to_slug(workdir)
    proj_dir = cc_projects_root() / slug
    if not proj_dir.is_dir():
        return []
    out: list[SessionSummary] = []
    for f in proj_dir.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
            title = _first_user_text(f)
        except OSError:
            # file vanished between glob and read (CC rotates its own logs)
            continue
        out.append(SessionSummary(
            cc_session_id=f.stem,
            title=title,
            updated_at=mtime,
        ))
    out.sort(key=lambda s: s.updated_at, reverse=True)
    if limit is not None:
        return out[:limit]
    return out


def _first_user_text(path: Path) -> str:
    """Scan the head of a session file for the first user text message."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(_TITLE_SCAN_LINES):
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "user":
                    continue
                content = ev.get("message", {}).get("content")
                text = _extract_text(content)
                if text:
                    return text
    except OSError:
        return ""
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
