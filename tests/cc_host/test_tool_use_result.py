"""Tests for cc_host.tool_use_result — convert CC's toolUseResult (the
structuredPatch cc computes at execution time) into our WriteDiff wire schema.

This is the crux of slice-033 feat 2 (方案 F): instead of the BE snapshotting
the file pre-write, we read the diff cc itself already computed and persisted
in jsonl (toolUseResult) / stream-json (tool_use_result).
"""

from __future__ import annotations

from trowel_py.cc_host.tool_use_result import write_diff_from_cc_result
from trowel_py.schemas.cc_host import DiffHunk, WriteDiff


# ── realistic samples (shapes verified against cc jsonl toolUseResult) ──

# Edit toolUseResult: NO "type" field, has structuredPatch with real file lines.
EDIT_TUR = {
    "filePath": "/a/service.py",
    "oldString": "    if not sid: return\n",
    "newString": "    if not sid:\n        return\n",
    "originalFile": "async def send(self, text):\n    sid = get().activeSid\n    if not sid: return\n    turn = build_turn(text)\n",
    "structuredPatch": [
        {
            "oldStart": 360,
            "oldLines": 2,
            "newStart": 360,
            "newLines": 3,
            "lines": [
                " async def send(self, text):",
                "-    if not sid: return",
                "+    if not sid:",
                "+        return",
            ],
        }
    ],
    "userModified": False,
    "replaceAll": False,
}

# Write-overwrite toolUseResult: type="update", structuredPatch present.
WRITE_UPDATE_TUR = {
    "type": "update",
    "filePath": "/a/proxy.py",
    "content": "new\n",
    "originalFile": "old\n",
    "structuredPatch": [
        {
            "oldStart": 1,
            "oldLines": 1,
            "newStart": 1,
            "newLines": 1,
            "lines": ["-old", "+new"],
        }
    ],
    "userModified": False,
}

# Write-create toolUseResult: type="create", structuredPatch empty.
WRITE_CREATE_TUR = {
    "type": "create",
    "filePath": "/a/new.py",
    "content": "a\nb\nc\n",
    "originalFile": "",
    "structuredPatch": [],
    "userModified": False,
}

# A non-Edit/Write toolUseResult (Bash carries stdout, no structuredPatch).
BASH_TUR = {
    "stdout": "ok\n",
    "interrupted": False,
    "sandbox": False,
}


def test_edit_tur_becomes_update_with_real_line_numbers() -> None:
    """Edit's toolUseResult (no type, structuredPatch present) → update diff,
    and the hunk carries the REAL file line numbers (360, not 1)."""
    wd = write_diff_from_cc_result(EDIT_TUR)
    assert wd is not None
    assert wd.type == "update"
    assert len(wd.hunks) == 1
    h = wd.hunks[0]
    assert h.oldStart == 360
    assert h.newStart == 360
    assert h.lines == (
        " async def send(self, text):",
        "-    if not sid: return",
        "+    if not sid:",
        "+        return",
    )


def test_write_update_tur_becomes_update() -> None:
    wd = write_diff_from_cc_result(WRITE_UPDATE_TUR)
    assert wd is not None
    assert wd.type == "update"
    assert wd.hunks[0].oldStart == 1
    assert wd.hunks[0].lines == ("-old", "+new")


def test_write_create_tur_becomes_create_with_empty_hunks() -> None:
    """Write-create: FE renders CreateBody from input.content, so we signal
    'create' with empty hunks (same wire shape as the old BE snapshot)."""
    wd = write_diff_from_cc_result(WRITE_CREATE_TUR)
    assert wd is not None
    assert wd.type == "create"
    assert wd.hunks == ()


def test_non_edit_write_tur_returns_none() -> None:
    """Bash/Read/... toolUseResults have no structuredPatch → None (FE keeps
    its existing rendering, no writeDiff attached)."""
    assert write_diff_from_cc_result(BASH_TUR) is None


def test_missing_structured_patch_returns_none() -> None:
    """Malformed toolUseResult (no structuredPatch key) → None, FE falls back
    to fragment diff. Never raises."""
    assert write_diff_from_cc_result({"filePath": "/a"}) is None


def test_empty_structured_patch_without_create_type_returns_none() -> None:
    """Edit that produced no patch (e.g. old_string==new_string no-op, or a
    failed edit) → empty structuredPatch, no type → None → FE fragment diff."""
    assert write_diff_from_cc_result({"structuredPatch": []}) is None


def test_returns_write_diff_schema_instance() -> None:
    """Sanity: the converter returns the pydantic WriteDiff wire schema (not a
    dataclass), so it can be attached to ToolResultEvent directly."""
    wd = write_diff_from_cc_result(EDIT_TUR)
    assert isinstance(wd, WriteDiff)
    assert isinstance(wd.hunks[0], DiffHunk)


def test_non_dict_input_returns_none() -> None:
    """Defensive: a tool_result whose toolUseResult is malformed (not a dict)
    → None, never raises."""
    assert write_diff_from_cc_result(None) is None  # type: ignore[arg-type]
    assert write_diff_from_cc_result("not a dict") is None  # type: ignore[arg-type]


def test_multi_hunk_patch_preserves_all_hunks_and_real_lines() -> None:
    """MultiEdit / far-apart changes → multiple hunks, each with its own real
    oldStart/newStart from cc."""
    tur = {
        "structuredPatch": [
            {"oldStart": 10, "oldLines": 1, "newStart": 10, "newLines": 1, "lines": ["-a", "+A"]},
            {"oldStart": 500, "oldLines": 1, "newStart": 501, "newLines": 1, "lines": ["-b", "+B"]},
        ]
    }
    wd = write_diff_from_cc_result(tur)
    assert wd is not None
    assert wd.type == "update"
    assert [h.oldStart for h in wd.hunks] == [10, 500]
    assert [h.newStart for h in wd.hunks] == [10, 501]
