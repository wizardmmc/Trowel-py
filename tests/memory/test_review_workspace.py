"""tests for the review workdir (slice-040 T10)."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.review_workspace import ensure_review_workdir, review_workdir_root


def test_ensure_creates_dir(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    wd = ensure_review_workdir("2026-07-09", memory_root=mem)
    assert wd == tmp_path / "review-daily-work" / "2026-07-09"
    assert wd.is_dir()


def test_ensure_git_init(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    wd = ensure_review_workdir("2026-07-09", memory_root=mem)
    assert (wd / ".git").is_dir()


def test_ensure_idempotent(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    wd1 = ensure_review_workdir("2026-07-09", memory_root=mem)
    wd2 = ensure_review_workdir("2026-07-09", memory_root=mem)
    assert wd1 == wd2  # second call must not raise


def test_review_workdir_root_is_sibling_of_memory(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    assert review_workdir_root(mem) == tmp_path / "review-daily-work"
