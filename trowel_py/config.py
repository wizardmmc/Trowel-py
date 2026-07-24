"""全局配置加载。"""

from __future__ import annotations
import tomllib
from pathlib import Path
from trowel_py.llm.client import LLMConfig


def _find_config_path() -> Path:
    """按工作目录、用户目录、源码树顺序查找未随 wheel 打包的敏感配置。"""
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
    """读取 ``[llm].active`` 指向的 provider 配置。"""
    if not path.exists():
        raise FileNotFoundError(f"config.toml not found at {path}.")
    with path.open("rb") as f:
        data = tomllib.load(f)
    llm = data["llm"]
    active = llm["active"]
    group = llm[active]
    return LLMConfig(**group)
