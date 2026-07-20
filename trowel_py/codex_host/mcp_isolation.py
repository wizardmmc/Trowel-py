"""slice-078: detect a same-named MCP server in the user's codex config.

Memory-off isolation (slice-078 §4 capability roster / C-3): ``--disable
memories`` only turns Codex native memories off — it does NOT unregister
user-configured MCP servers in ``~/.codex/config.toml``. So if a user has
configured a server with the same name trowel registers (default
``trowel_note_search``), the app-server would still spawn it on a memory-off
thread, making "memory-off" a lie. This module reads the user config and
surfaces that collision so the hub can refuse to create the session rather
than ship a contaminated experiment (spec: "must detect and explicitly
fail/isolate, not claim off").

Read-only: never writes ``config.toml``. Errors are tolerated — a missing or
unparseable file reports no conflict, so a broken user config never blocks
session creation; only an actual same-name entry does.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME

_log = logging.getLogger(__name__)

#: The codex config file name inside ``CODEX_HOME`` (codex convention).
CONFIG_TOML = "config.toml"

#: Cap for the ``git rev-parse`` subprocess used to find the repo root.
_GIT_TIMEOUT_S = 5.0


def resolve_codex_config_path(
    codex_home: str | os.PathLike | None = None,
) -> Path:
    """Return the codex ``config.toml`` path.

    Args:
        codex_home: Override ``CODEX_HOME`` (tests pass a tmp dir). ``None``
            reads the ``CODEX_HOME`` env var, falling back to ``~/.codex``.

    Returns:
        Absolute path to ``<codex_home>/config.toml``.
    """

    if codex_home is not None:
        home = Path(codex_home)
    else:
        env_home = os.environ.get("CODEX_HOME")
        home = Path(env_home) if env_home else Path.home() / ".codex"
    return home / CONFIG_TOML


@dataclass(frozen=True)
class McpConflict:
    """One same-named MCP server found in the user's codex config.

    Attributes:
        server_name: The colliding name (equals the trowel-registered name).
        config_path: The ``config.toml`` that declares it (for the error msg).
    """

    server_name: str
    config_path: str


def find_conflicting_mcp_server(
    server_name: str = TROWEL_NOTE_SEARCH_SERVER_NAME,
    *,
    codex_home: str | os.PathLike | None = None,
    workdir: str | os.PathLike | None = None,
) -> McpConflict | None:
    """Return a conflict if ``server_name`` is declared in any codex config layer.

    Args:
        server_name: The MCP server name trowel is about to register.
        codex_home: Override ``CODEX_HOME`` (tests pass a tmp dir).
        workdir: slice-078 HIGH-4 (codex review) — the session's cwd. Codex
            loads project-level config (``<workdir>/.codex/config.toml`` and
            ``<git-root>/.codex/config.toml``) and deep-merges it with the
            global config, so a same-named server in any layer contaminates a
            memory-off session. When ``workdir`` is ``None`` only the global
            layer is checked (back-compat).

    Returns:
        :class:`McpConflict` when the name is already taken in any layer;
        ``None`` when every layer is absent, unparseable, or has no such server.

    Concurrency: this is a single-shot read with no locking. If another
    process is mid-write to ``config.toml`` at the instant we read, the
    half-written TOML parses as ``TOMLDecodeError`` and we tolerate it as
    "no conflict" — the user finishes editing and the next session creation
    re-reads the consistent file. Failing the session on a transient parse
    error would punish the user for editing their own config.
    """

    for path in _collect_config_paths(codex_home, workdir):
        conflict = _check_one_layer(path, server_name)
        if conflict is not None:
            return conflict
    return None


def _collect_config_paths(
    codex_home: str | os.PathLike | None,
    workdir: str | os.PathLike | None,
) -> list[Path]:
    """Collect every codex ``config.toml`` path codex would load for this cwd.

    Codex loads (config/loader/mod.rs:103-104): global (``$CODEX_HOME`` /
    ``~/.codex``), tree (parent dirs up to root with ``.codex/config.toml``),
    and repo (``$(git rev-parse --show-toplevel)/.codex/config.toml``). We
    check global + workdir + git-root; the full parent-walk is bounded by the
    repo boundary in practice for trusted layers, so workdir + git-root cover
    the realistic cases. Duplicates are removed so a workdir that IS the git
    root reads its ``.codex/config.toml`` exactly once.
    """

    paths = [resolve_codex_config_path(codex_home)]
    if workdir is not None:
        workdir_path = Path(workdir).resolve() / ".codex" / CONFIG_TOML
        paths.append(workdir_path)
        git_root = _git_root(workdir)
        if git_root is not None:
            git_path = git_root / ".codex" / CONFIG_TOML
            if git_path != workdir_path:
                paths.append(git_path)
    # Dedup while preserving first-seen order (global first).
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _git_root(workdir: str | os.PathLike) -> Path | None:
    """Return the git toplevel for ``workdir``, or None if not a repo / git fails.

    Never raises — a non-repo workdir or a missing ``git`` binary simply means
    no repo layer to check.
    """

    try:
        result = subprocess.run(  # noqa: S603,S607 — trusted argv, git on PATH
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None


def _check_one_layer(path: Path, server_name: str) -> McpConflict | None:
    """Read one ``config.toml`` and return a conflict if the server is declared."""

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError):
        _log.warning(
            "codex config at %s is unreadable; skipping MCP isolation check",
            path,
        )
        return None
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    if server_name in servers:
        return McpConflict(server_name=server_name, config_path=str(path))
    return None
