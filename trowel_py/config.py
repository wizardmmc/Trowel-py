"""
global config loading
"""
from __future__ import annotations
import tomllib
from pathlib import Path
from trowel_py.llm.client import LLMConfig

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

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