"""Convert CC's ``toolUseResult`` into our ``WriteDiff`` wire schema.

slice-033 feat 2 (方案 F): the BE no longer snapshots the file pre-write to
compute a diff. Instead it reads the structuredPatch **cc itself already
computed at tool-execution time** and persisted in
  - jsonl:        ``toolUseResult``        (camelCase, replay path)
  - stream-json:  ``tool_use_result``      (snake_case, live path)

Both carry the same shape (cc's ``FileEditOutput``:
``{filePath, originalFile, structuredPatch, type?, ...}``). cc computes the
patch against the real file, so ``oldStart``/``newStart`` are real file line
numbers — and because it's persisted in jsonl, replay renders identically to
live, even after a BE restart (the thing the old in-memory cache couldn't do).

This module is the single place that knows how to turn a cc toolUseResult dict
into a ``WriteDiff``. Callers (``history.py`` for replay, ``translator.py`` for
live) pass the dict; the result is attached to ``ToolResultEvent.write_diff``.
"""

from __future__ import annotations

from typing import Any

from trowel_py.schemas.cc_host import DiffHunk, WriteDiff


def _convert_hunks(patch: Any) -> tuple[DiffHunk, ...]:
    """Turn cc's ``structuredPatch`` (list of hunk dicts) into schema DiffHunks.

    cc's hunk shape (FileEditTool/types.ts ``hunkSchema``) is identical to our
    ``DiffHunk`` wire schema: ``oldStart``/``oldLines``/``newStart``/``newLines``
    /``lines`` (lines carry the leading ``' '/'+ '-'`` marker). Defensive on
    shape — a malformed patch yields an empty tuple, never raises.

    Args:
        patch: the ``structuredPatch`` value from a cc toolUseResult (a list of
            hunk dicts, or anything else).

    Returns:
        a tuple of ``DiffHunk`` (empty when ``patch`` is missing/malformed).
    """
    if not isinstance(patch, list):
        return ()
    out: list[DiffHunk] = []
    for h in patch:
        if not isinstance(h, dict):
            continue
        raw_lines = h.get("lines", [])
        lines = tuple(str(ln) for ln in raw_lines) if isinstance(raw_lines, list) else ()
        out.append(
            DiffHunk(
                oldStart=int(h.get("oldStart", 0) or 0),
                oldLines=int(h.get("oldLines", 0) or 0),
                newStart=int(h.get("newStart", 0) or 0),
                newLines=int(h.get("newLines", 0) or 0),
                lines=lines,
            )
        )
    return tuple(out)


def write_diff_from_cc_result(tool_use_result: Any) -> WriteDiff | None:
    """Map a cc ``toolUseResult`` dict to a ``WriteDiff`` for the FE.

    Classification does NOT need the tool name — it falls out of the dict's
    own shape:
      - ``type == "create"`` (Write-create) → ``WriteDiff(type="create")`` with
        empty hunks. The FE renders CreateBody from ``input.content``; the
        patch is empty anyway (cc has no old file to diff against).
      - ``structuredPatch`` non-empty → ``WriteDiff(type="update", hunks=…)``.
        Covers Edit/MultiEdit (no ``type`` field) and Write-overwrite
        (``type="update"``). Hunk line numbers are cc's real file line numbers.
      - otherwise → ``None`` (Bash/Read/… toolUseResults; a failed/empty Edit;
        a malformed dict). The FE falls back to its existing rendering — for
        Edit that's the fragment diff computed from old/new_string (line numbers
        from 1), keeping the failure mode graceful.

    Args:
        tool_use_result: the cc ``toolUseResult`` / ``tool_use_result`` dict.
            Anything non-dict → ``None`` (defensive, never raises).

    Returns:
        a ``WriteDiff`` (create/update), or ``None`` when there's nothing to
        attach.
    """
    if not isinstance(tool_use_result, dict):
        return None

    # A non-empty structuredPatch wins over the `type` tag — covers
    # Edit/MultiEdit (no type field) and Write-overwrite (type='update') with
    # real file line numbers. type='create' with an empty patch → create; a
    # malformed type='create' that still carries a patch defers to the patch.
    hunks = _convert_hunks(tool_use_result.get("structuredPatch"))
    if hunks:
        return WriteDiff(type="update", hunks=hunks)

    if tool_use_result.get("type") == "create":
        return WriteDiff(type="create", hunks=())

    return None
