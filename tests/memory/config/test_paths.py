from __future__ import annotations

import textwrap
from pathlib import Path

from trowel_py.memory import paths


def _write_config(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _set_config_search_roots(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    workdir = tmp_path / "workdir"
    home = tmp_path / "home"
    workdir.mkdir()
    (home / ".trowel").mkdir(parents=True)
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TROWEL_DATA_ROOT", raising=False)
    return workdir / "config.toml", home / ".trowel" / "config.toml"


def _use_browser_home(tmp_path: Path, monkeypatch) -> Path:
    """为浏览器默认路径测试提供不含桌面覆盖的临时用户目录。"""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TROWEL_DATA_ROOT", raising=False)
    return home


def test_default_root_when_config_missing(tmp_path: Path, monkeypatch) -> None:
    home = _use_browser_home(tmp_path, monkeypatch)
    missing = tmp_path / "nope.toml"
    assert paths.resolve_memory_root(missing) == home / ".trowel" / "memory"


def test_default_root_when_no_memory_section(tmp_path: Path, monkeypatch) -> None:
    home = _use_browser_home(tmp_path, monkeypatch)
    cfg = _write_config(
        tmp_path / "config.toml",
        """
        [llm]
        active = "glm"
        """,
    )
    assert paths.resolve_memory_root(cfg) == home / ".trowel" / "memory"


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
    assert not wanted.exists()


def test_override_expands_user(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _write_config(
        tmp_path / "config.toml",
        """
        [memory]
        root = "~/m"
        """,
    )
    assert paths.resolve_memory_root(cfg) == tmp_path / "m"


def test_find_config_path_prefers_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    cwd_config, home_config = _set_config_search_roots(tmp_path, monkeypatch)
    _write_config(cwd_config, "")
    _write_config(home_config, "")

    assert paths.find_config_path() == cwd_config


def test_find_config_path_uses_home_when_working_directory_missing(
    tmp_path: Path, monkeypatch
) -> None:
    _, home_config = _set_config_search_roots(tmp_path, monkeypatch)
    _write_config(home_config, "")

    assert paths.find_config_path() == home_config


def test_find_config_path_returns_home_candidate_when_both_missing(
    tmp_path: Path, monkeypatch
) -> None:
    _, home_config = _set_config_search_roots(tmp_path, monkeypatch)

    assert paths.find_config_path() == home_config
