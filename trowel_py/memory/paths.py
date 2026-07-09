"""resolve the memory store root directory (slice-038).

The memory tree lives under ``~/.trowel/memory`` by default — the same XDG-style
home dir that holds ``config.toml``. A user can relocate it with
``[memory] root = "..."`` in ``config.toml``.

This module resolves the path only; it never creates directories (the store
does that lazily on write).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

#: Default root: ``~/.trowel/memory``.
DEFAULT_MEMORY_ROOT = Path.home() / ".trowel" / "memory"


def _candidate_config_paths() -> list[Path]:
    """Config search order, mirroring ``trowel_py.config``."""
    return [Path.cwd() / "config.toml", Path.home() / ".trowel" / "config.toml"]


def _find_config_path() -> Path:
    for c in _candidate_config_paths():
        if c.exists():
            return c
    return _candidate_config_paths()[-1]


def resolve_memory_root(config_path: Path | None = None) -> Path:
    """Return the memory root, honoring ``[memory] root`` in config.toml if set.

    Args:
        config_path: optional explicit config.toml location for testability.
            Defaults to the standard lookup (cwd then ``~/.trowel``).

    Returns:
        The resolved memory root directory. ``~`` is expanded against ``$HOME``.
        The directory is NOT created here.
    """
    path = config_path or _find_config_path()
    if not path.exists():
        return Path.home() / ".trowel" / "memory"
    with path.open("rb") as f:
        data = tomllib.load(f)
    override = data.get("memory", {}).get("root")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".trowel" / "memory"
