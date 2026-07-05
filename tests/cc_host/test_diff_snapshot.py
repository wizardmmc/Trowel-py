"""Tests for cc_host.diff_snapshot — BE-side Write-overwrite diff.

compute_write_diff produces hunks in the same shape as jsdiff's
StructuredPatchHunk so the FE renders Edit (FE-computed) and Write-overwrite
(BE-computed) through the same component (slice-029 reload consistency).
"""
from __future__ import annotations

from trowel_py.cc_host.diff_snapshot import (
    CONTEXT_LINES,
    DiffHunk,
    WriteDiff,
    compute_write_diff,
)


class TestCreateVsUpdate:
    def test_old_none_means_create_no_hunks(self) -> None:
        wd = compute_write_diff(None, "fresh\ncontent\n")
        assert wd.type == "create"
        assert wd.hunks == ()

    def test_old_present_means_update(self) -> None:
        wd = compute_write_diff("a\nb\n", "a\nB\n")
        assert wd.type == "update"
        assert len(wd.hunks) >= 1


class TestHunkShape:
    def test_mixed_change_one_hunk_with_plus_and_minus(self) -> None:
        wd = compute_write_diff("alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n")
        assert len(wd.hunks) == 1
        h = wd.hunks[0]
        assert h.oldStart == 1
        assert h.newStart == 1
        assert any(ln.startswith("+") for ln in h.lines)
        assert any(ln.startswith("-") for ln in h.lines)
        assert any(ln.startswith(" ") for ln in h.lines)

    def test_lines_carry_marker_prefix_without_trailing_newline(self) -> None:
        wd = compute_write_diff("a\nb\n", "a\nc\n")
        for ln in wd.hunks[0].lines:
            assert "\n" not in ln
            assert ln[0] in (" ", "+", "-")

    def test_pure_addition_has_no_minus_lines(self) -> None:
        wd = compute_write_diff("a\nb\n", "a\nb\nc\n")
        all_lines = [ln for h in wd.hunks for ln in h.lines]
        assert any(ln.startswith("+") for ln in all_lines)
        assert not any(ln.startswith("-") for ln in all_lines)

    def test_pure_removal_has_no_plus_lines(self) -> None:
        wd = compute_write_diff("a\nb\nc\n", "a\nb\n")
        all_lines = [ln for h in wd.hunks for ln in h.lines]
        assert any(ln.startswith("-") for ln in all_lines)
        assert not any(ln.startswith("+") for ln in all_lines)


class TestMultiHunk:
    def test_far_apart_changes_produce_multiple_hunks(self) -> None:
        head = "\n".join(f"l{i}" for i in range(8)) + "\n"
        old = f"{head}REMOVE1\n{head}REMOVE2\n"
        new = f"{head}{head}"
        wd = compute_write_diff(old, new)
        assert len(wd.hunks) >= 2

    def test_identical_content_yields_no_hunks(self) -> None:
        wd = compute_write_diff("same\n", "same\n")
        assert wd.type == "update"
        assert wd.hunks == ()


class TestCounts:
    """Counts must match what the FE's summarizeStat produces from the same
    hunks — this is the cross-stack invariant (Edit FE-counts, Write BE-counts,
    same render)."""

    def _stat(self, wd: WriteDiff) -> tuple[int, int]:
        add = sum(1 for h in wd.hunks for ln in h.lines if ln.startswith("+"))
        rm = sum(1 for h in wd.hunks for ln in h.lines if ln.startswith("-"))
        return add, rm

    def test_add_count_matches_added_lines(self) -> None:
        wd = compute_write_diff("a\nb\n", "a\nb\nc\nd\n")
        assert self._stat(wd) == (2, 0)

    def test_remove_count_matches_removed_lines(self) -> None:
        wd = compute_write_diff("a\nb\nc\nd\n", "a\n")
        add, rm = self._stat(wd)
        assert rm == 3
        assert add == 0

    def test_mixed_counts(self) -> None:
        wd = compute_write_diff("x\ny\nz\n", "x\nY\nZ\n")
        assert self._stat(wd) == (2, 2)


class TestFileRelativeLineNumbers:
    """BE has the full file → hunk.oldStart is the real file line (1-based),
    not fragment-relative like FE's Edit diff."""

    def test_oldstart_reflects_file_position(self) -> None:
        # Change is at old line 5; with context=3 the hunk still starts at 1
        # (5-3=2, but context clamps). Use a longer file so the start shifts.
        old = "\n".join(f"line{i}" for i in range(20)) + "\n"
        new_lines = [f"line{i}" for i in range(20)]
        new_lines[15] = "CHANGED"
        new = "\n".join(new_lines) + "\n"
        wd = compute_write_diff(old, new)
        assert len(wd.hunks) == 1
        # context=3 around line 16 (1-based) → starts at line 13.
        assert wd.hunks[0].oldStart == 13


def test_context_lines_constant_matches_cc() -> None:
    # CC `utils/diff.ts::CONTEXT_LINES = 3` — BE must use the same.
    assert CONTEXT_LINES == 3
