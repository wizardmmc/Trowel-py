"""定位 ``config.toml`` 并解析 Memory 根目录；不会创建目录。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from trowel_py.application_paths import (
    has_application_data_root_override,
    resolve_application_data_root,
)


def _candidate_config_paths() -> list[Path]:
    """按当前工作目录、用户配置目录的顺序返回 ``config.toml`` 候选路径。"""
    application_config = resolve_application_data_root() / "config.toml"
    if has_application_data_root_override():
        return [application_config]
    return [Path.cwd() / "config.toml", application_config]


def _find_config_path() -> Path:
    """选择首个存在的候选配置文件，两处均缺失时返回用户配置路径。"""
    for c in _candidate_config_paths():
        if c.exists():
            return c
    return _candidate_config_paths()[-1]


def find_config_path() -> Path:
    """返回调用方应读取的 ``config.toml`` 路径。

    当前工作目录优先于 ``~/.trowel``；两处均无配置文件时，仍返回
    ``~/.trowel/config.toml``。
    """
    return _find_config_path()


def resolve_memory_root(config_path: Path | None = None) -> Path:
    """从配置文件读取 ``[memory] root`` 指定的 Memory 根目录。

    配置文件缺失，或 ``[memory] root`` 未设置或解析后为假值时，返回
    ``~/.trowel/memory``。函数会展开路径开头的 ``~``，但不会创建目录。
    相对路径保持为相对 ``Path``，不会以配置文件所在目录为基准解析。

    Args:
        config_path: 要读取的 ``config.toml``；为 ``None`` 时按当前工作目录、
            ``~/.trowel`` 的顺序查找。

    Returns:
        配置指定的 Memory 根目录，或未配置时的默认目录。

    Raises:
        OSError: 无法打开或读取配置路径。
        UnicodeDecodeError: 配置内容不是有效的 UTF-8。
        tomllib.TOMLDecodeError: 配置文件不是有效的 TOML。
        AttributeError: ``memory`` 已配置，但不是 TOML 表。
        TypeError: ``[memory] root`` 是非空的非字符串值，无法转换为路径。
        RuntimeError: ``[memory] root`` 中的用户主目录无法展开。
    """
    if config_path is None and has_application_data_root_override():
        return resolve_application_data_root() / "memory"
    path = config_path or _find_config_path()
    if not path.exists():
        return resolve_application_data_root() / "memory"
    with path.open("rb") as f:
        data = tomllib.load(f)
    override = data.get("memory", {}).get("root")
    if override:
        return Path(override).expanduser()
    return resolve_application_data_root() / "memory"
