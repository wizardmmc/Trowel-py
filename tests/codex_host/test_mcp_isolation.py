"""slice-078: memory-MCP isolation check against the user's codex config."""
from __future__ import annotations

import os
from pathlib import Path

from trowel_py.codex_host.mcp_isolation import (
    find_conflicting_mcp_server,
    resolve_codex_config_path,
)
from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME


def _write_config(codex_home: Path, body: str) -> None:
    """Write a config.toml under ``codex_home`` (creating the directory)."""
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(body, encoding="utf-8")


def test_resolve_defaults_to_home_codex_dir(monkeypatch) -> None:
    """No CODEX_HOME, no override → ~/.codex/config.toml."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # Path.home() is the default; just assert the shape, not the real $HOME.
    assert resolve_codex_config_path() == Path.home() / ".codex" / "config.toml"


def test_resolve_reads_codex_home_env(monkeypatch, tmp_path: Path) -> None:
    """CODEX_HOME env overrides ~/.codex."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom"))
    assert resolve_codex_config_path() == tmp_path / "custom" / "config.toml"


def test_resolve_explicit_arg_beats_env(
    monkeypatch, tmp_path: Path
) -> None:
    """An explicit codex_home arg wins over CODEX_HOME env."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "env"))
    assert resolve_codex_config_path(tmp_path / "arg") == tmp_path / "arg" / "config.toml"


def test_no_config_file_means_no_conflict(tmp_path: Path) -> None:
    """A missing config.toml is the common case — never reports a conflict."""
    assert find_conflicting_mcp_server(codex_home=tmp_path) is None


def test_config_with_other_servers_no_conflict(tmp_path: Path) -> None:
    """An unrelated mcp_servers block is fine."""
    _write_config(
        tmp_path,
        "[mcp_servers.github]\ncommand = 'gh-mcp'\n",
    )
    assert find_conflicting_mcp_server(codex_home=tmp_path) is None


def test_config_with_same_name_server_is_conflict(tmp_path: Path) -> None:
    """A same-named entry is the collision the check exists to catch."""
    _write_config(
        tmp_path,
        f"[mcp_servers.{TROWEL_NOTE_SEARCH_SERVER_NAME}]\n"
        f"command = 'python'\n",
    )
    conflict = find_conflicting_mcp_server(codex_home=tmp_path)
    assert conflict is not None
    assert conflict.server_name == TROWEL_NOTE_SEARCH_SERVER_NAME
    assert conflict.config_path == str(tmp_path / "config.toml")


def test_config_with_empty_mcp_servers_no_conflict(tmp_path: Path) -> None:
    """An [mcp_servers] table present but empty is not a conflict."""
    _write_config(tmp_path, "[mcp_servers]\n")
    assert find_conflicting_mcp_server(codex_home=tmp_path) is None


def test_unparseable_config_does_not_block(tmp_path: Path) -> None:
    """A corrupt config.toml is tolerated (no conflict), never raises —
    a broken user config must not block session creation."""
    _write_config(tmp_path, "this is not = = valid toml }}}")
    assert find_conflicting_mcp_server(codex_home=tmp_path) is None


def test_custom_server_name_can_be_checked(tmp_path: Path) -> None:
    """The check is parametric — a caller can probe any name."""
    _write_config(tmp_path, "[mcp_servers.my_tool]\ncommand = 'x'\n")
    conflict = find_conflicting_mcp_server("my_tool", codex_home=tmp_path)
    assert conflict is not None
    assert conflict.server_name == "my_tool"


# ---- project-level layers (slice-078 HIGH-4, codex review) ----


def test_workdir_level_same_name_is_conflict(tmp_path: Path) -> None:
    """A same-named server in ``<workdir>/.codex/config.toml`` is also a
    collision — codex deep-merges project config into the effective set."""
    workdir = tmp_path / "proj"
    (workdir / ".codex").mkdir(parents=True)
    (workdir / ".codex" / "config.toml").write_text(
        f"[mcp_servers.{TROWEL_NOTE_SEARCH_SERVER_NAME}]\ncommand = 'x'\n",
        encoding="utf-8",
    )
    conflict = find_conflicting_mcp_server(
        codex_home=tmp_path / "global-empty", workdir=workdir
    )
    assert conflict is not None
    assert conflict.config_path == str(workdir / ".codex" / "config.toml")


def test_workdir_level_unrelated_no_conflict(tmp_path: Path) -> None:
    """An unrelated server name in the workdir layer is fine."""
    workdir = tmp_path / "proj"
    (workdir / ".codex").mkdir(parents=True)
    (workdir / ".codex" / "config.toml").write_text(
        "[mcp_servers.github]\ncommand = 'gh'\n", encoding="utf-8"
    )
    assert (
        find_conflicting_mcp_server(
            codex_home=tmp_path / "global-empty", workdir=workdir
        )
        is None
    )


def test_workdir_none_skips_project_layers(tmp_path: Path) -> None:
    """workdir=None → only the global layer is checked (back-compat)."""
    assert find_conflicting_mcp_server(codex_home=tmp_path, workdir=None) is None


def test_global_conflict_reported_even_when_workdir_clean(tmp_path: Path) -> None:
    """The global layer is still checked when a workdir is given."""
    global_home = tmp_path / "global"
    _write_config(
        global_home,
        f"[mcp_servers.{TROWEL_NOTE_SEARCH_SERVER_NAME}]\ncommand = 'x'\n",
    )
    workdir = tmp_path / "proj"
    workdir.mkdir()
    conflict = find_conflicting_mcp_server(codex_home=global_home, workdir=workdir)
    assert conflict is not None
    assert conflict.config_path == str(global_home / "config.toml")
