"""the daily-review cc workdir (slice-040 T10).

The distillation cc agent runs in ``~/.trowel/review-daily-work/{date}/`` — a
clean workdir with no project ``CLAUDE.md`` to pollute the agent, ``git init``'d
to avoid cc's "not a repo" warning. It is a sibling of ``memory/`` (both under
``~/.trowel/``). Persistent (dated) so ``draft.json`` history survives for
debugging.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from trowel_py.memory.paths import resolve_memory_root

logger = logging.getLogger(__name__)

_REVIEW_DIR_NAME = "review-daily-work"


def review_workdir_root(memory_root: Path | None = None) -> Path:
    """Return the review-daily-work root (sibling of memory/, under ~/.trowel)."""
    root = memory_root if memory_root is not None else resolve_memory_root()
    return root.parent / _REVIEW_DIR_NAME


def ensure_review_workdir(date_str: str, memory_root: Path | None = None) -> Path:
    """Create (idempotently) the dated review workdir for ``date_str``.

    Runs ``git init`` if the dir has no ``.git``, so cc does not warn "not a
    repo" when the agent runs there. ``git init`` is best-effort: a failure
    (git missing, etc.) is logged and swallowed — it only affects a cosmetic
    cc warning, not correctness.

    Returns:
        The absolute workdir path.
    """
    workdir = review_workdir_root(memory_root) / date_str
    workdir.mkdir(parents=True, exist_ok=True)
    if not (workdir / ".git").exists():
        try:
            subprocess.run(
                ["git", "init"],
                cwd=str(workdir),
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("git init failed for %s (ignored): %s", workdir, exc)
    return workdir
