"""Memory 根目录与 ``config.toml`` 查找规则；只解析路径，不创建目录。"""

from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_MEMORY_ROOT = Path.home() / ".trowel" / "memory"


def _candidate_config_paths() -> list[Path]:
    """按工作目录、用户配置目录的顺序返回候选。"""
    return [Path.cwd() / "config.toml", Path.home() / ".trowel" / "config.toml"]


def _find_config_path() -> Path:
    for c in _candidate_config_paths():
        if c.exists():
            return c
    return _candidate_config_paths()[-1]


def find_config_path() -> Path:
    """返回首个存在的配置；均缺失时返回用户配置路径。"""
    return _find_config_path()


def resolve_memory_root(config_path: Path | None = None) -> Path:
    """解析 ``[memory] root``，未配置时返回默认路径；不创建目录。"""
    path = config_path or _find_config_path()
    if not path.exists():
        return Path.home() / ".trowel" / "memory"
    with path.open("rb") as f:
        data = tomllib.load(f)
    override = data.get("memory", {}).get("root")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".trowel" / "memory"
