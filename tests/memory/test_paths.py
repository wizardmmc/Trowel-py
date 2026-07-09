"""tests for memory root resolution (slice-038 T1)."""
from __future__ import annotations

import textwrap
from pathlib import Path

from trowel_py.memory import paths


def _write_config(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_default_root_when_config_missing(tmp_path: Path) -> None:
    # config path that does not exist -> default home-based root.
    missing = tmp_path / "nope.toml"
    assert paths.resolve_memory_root(missing) == Path.home() / ".trowel" / "memory"


def test_default_root_when_no_memory_section(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path / "config.toml",
        """
        [llm]
        active = "glm"
        """,
    )
    assert paths.resolve_memory_root(cfg) == Path.home() / ".trowel" / "memory"


def test_override_from_memory_root(tmp_path: Path) -> None:
    wanted = tmp_path / "custom-memory"
    cfg = _write_config(
        tmp_path / "config.toml",
        f"""
        [memory]
        root = "{wanted}"
        """,
    )
    assert paths.resolve_memory_root(cfg) == wanted


def test_override_expands_user(tmp_path: Path, monkeypatch) -> None:
    # a "~"-relative override resolves against $HOME (expanduser semantics).
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _write_config(
        tmp_path / "config.toml",
        """
        [memory]
        root = "~/m"
        """,
    )
    assert paths.resolve_memory_root(cfg) == tmp_path / "m"
