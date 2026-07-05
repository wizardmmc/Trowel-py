"""
global config loading
"""
from __future__ import annotations
import tomllib
from pathlib import Path
from trowel_py.llm.client import LLMConfig

def _find_config_path() -> Path:
    """Locate config.toml across dev and installed layouts.

    Order: ``cwd/config.toml`` (wherever the user runs ``trowel-py``),
    ``~/.trowel/config.toml`` (home-level, XDG-style), then the project root
    next to the package (dev mode — works because the editable/checkout tree
    has config.toml at the repo root). The first existing one wins; the last
    is the default so the error message points somewhere sensible.

    config.toml carries API keys and is gitignored, so it is never bundled
    with the wheel — the user places it in one of these locations.
    """
    here = Path(__file__).resolve().parent.parent
    candidates = [
        Path.cwd() / "config.toml",
        Path.home() / ".trowel" / "config.toml",
        here / "config.toml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


_CONFIG_PATH = _find_config_path()


def load_llm_config(path: Path = _CONFIG_PATH) -> LLMConfig:
    """
    read config.toml, select the provider group named by [llm].active, build a LLMConfig

    Args:
        path: path to config.toml; defaults to the project root, injectable for tests.

    Returns:
        The LLMConfig of the active group.

    Raises:
        FileNotFoundError: config.toml is missing (hint: copy from the example).
        KeyError: the group named by `active` does not exist in the file.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"config.toml not found at {path}."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    llm = data["llm"]
    active = llm["active"]
    group = llm[active]
    return LLMConfig(**group)