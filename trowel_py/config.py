"""查找 ``config.toml``，并加载当前启用的模型连接配置。"""

from __future__ import annotations
import tomllib
from pathlib import Path

from trowel_py.application_paths import (
    has_application_data_root_override,
    resolve_application_data_root,
)
from trowel_py.llm.client import LLMConfig


def _find_config_path() -> Path:
    """按工作目录、用户配置目录和源码根目录的顺序选择配置文件。

    Returns:
        第一个已存在的 ``config.toml``；都不存在时返回源码根目录下的候选路径。
    """
    here = Path(__file__).resolve().parent.parent
    application_config = resolve_application_data_root() / "config.toml"
    candidates = (
        [application_config]
        if has_application_data_root_override()
        else [Path.cwd() / "config.toml", application_config, here / "config.toml"]
    )
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


def load_llm_config(path: Path | None = None) -> LLMConfig:
    """读取 ``[llm].active`` 指定的模型连接配置。

    Args:
        path: 要读取的 ``config.toml``；省略时按当前应用数据环境动态查找。

    Returns:
        当前启用模型的供应商、模型名称和连接设置。

    Raises:
        FileNotFoundError: 配置文件不存在。
        tomllib.TOMLDecodeError: 配置文件不是有效的 TOML。
        KeyError: ``[llm]``、``active`` 或对应的模型配置不存在。
        pydantic.ValidationError: 当前模型配置缺少必填字段，或字段值不符合
            ``LLMConfig`` 的约束。
    """
    resolved_path = _find_config_path() if path is None else path
    if not resolved_path.exists():
        raise FileNotFoundError(f"config.toml not found at {resolved_path}.")
    with resolved_path.open("rb") as f:
        data = tomllib.load(f)
    llm = data["llm"]
    active = llm["active"]
    group = llm[active]
    return LLMConfig(**group)
