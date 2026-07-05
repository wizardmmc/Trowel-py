"""BE-side Write-overwrite diff (slice-029 Phase 2).

When cc emits a Write tool_use, the host snapshots the file at that moment
(before cc writes), computes a structured diff against ``input.content``, and
attaches it to the ToolCallEvent. The hunks are jsdiff StructuredPatchHunk-
shaped so the FE renders Edit (FE-computed) and Write-overwrite (BE-computed)
through the same component — this is the cross-stack invariant that makes
live and reload render identically.

Uses stdlib ``difflib.SequenceMatcher.get_grouped_opcodes(context)`` which
groups changes with N surrounding context lines and splits far-apart changes
into separate hunks — the same semantics as jsdiff's ``structuredPatch``.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

#: Context lines around each change — matches CC ``utils/diff.ts`` and the FE.
CONTEXT_LINES = 3


@dataclass(frozen=True)
class DiffHunk:
    """One diff hunk — jsdiff StructuredPatchHunk shape.

    ``lines`` carry the leading marker char: ``' ctx'``, ``'+add'``, ``'-rm'``
    (no trailing newline). ``oldStart``/``newStart`` are 1-based file line
    numbers (BE has the full file, unlike FE's fragment-relative Edit diff).
    """

    oldStart: int
    oldLines: int
    newStart: int
    newLines: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class WriteDiff:
    """``type='create'`` (new file) carries no hunks; ``type='update'`` carries
    the real diff. The FE picks the render mode off ``type``."""

    type: str  # "create" | "update"
    hunks: tuple[DiffHunk, ...]


def _split_lines(s: str) -> list[str]:
    """Split on newlines; a trailing newline is a terminator (not a new empty
    line), matching CC's ``countLines`` and the FE's ``countLines``."""
    parts = s.split("\n")
    if parts and parts[-1] == "" and s.endswith("\n"):
        parts.pop()
    return parts


def _structured_hunks(old: str, new: str, context: int) -> tuple[DiffHunk, ...]:
    """Compute structured hunks (jsdiff-compatible) for old→new."""
    a = _split_lines(old)
    b = _split_lines(new)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    hunks: list[DiffHunk] = []
    for group in matcher.get_grouped_opcodes(context):
        first = group[0]
        last = group[-1]
        old_start = first[1]  # 0-based inclusive
        new_start = first[3]
        old_end = last[2]  # 0-based exclusive
        new_end = last[4]
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(" " + ln for ln in a[i1:i2])
            elif tag == "delete":
                lines.extend("-" + ln for ln in a[i1:i2])
            elif tag == "insert":
                lines.extend("+" + ln for ln in b[j1:j2])
            elif tag == "replace":
                lines.extend("-" + ln for ln in a[i1:i2])
                lines.extend("+" + ln for ln in b[j1:j2])
        hunks.append(
            DiffHunk(
                oldStart=old_start + 1,  # 1-based
                oldLines=old_end - old_start,
                newStart=new_start + 1,
                newLines=new_end - new_start,
                lines=tuple(lines),
            )
        )
    return tuple(hunks)


def compute_write_diff(old: str | None, new: str) -> WriteDiff:
    """Snapshot-style diff for a Write tool_use.

    Args:
        old: the pre-write file content, or ``None`` when the file did not
            exist (a fresh create).
        new: ``input.content`` of the Write tool_use.

    Returns:
        ``WriteDiff(type='create', hunks=())`` for new files; otherwise
        ``WriteDiff(type='update', hunks=...)``. A no-op write (old==new)
        yields ``type='update'`` with empty hunks.
    """
    if old is None:
        return WriteDiff(type="create", hunks=())
    return WriteDiff(type="update", hunks=_structured_hunks(old, new, CONTEXT_LINES))
