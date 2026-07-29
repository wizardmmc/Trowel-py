"""查找 ``config.toml``，并加载当前启用的模型连接配置。"""

from __future__ import annotations
import tomllib
from pathlib import Path
from trowel_py.llm.client import LLMConfig


def _find_config_path() -> Path:
    """按工作目录、用户配置目录和源码根目录的顺序选择配置文件。

    Returns:
        第一个已存在的 ``config.toml``；都不存在时返回源码根目录下的候选路径。
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
    """读取 ``[llm].active`` 指定的模型连接配置。

    Args:
        path: 要读取的 ``config.toml``；默认使用本模块导入时选定的路径。

    Returns:
        当前启用模型的供应商、模型名称和连接设置。

    Raises:
        FileNotFoundError: 配置文件不存在。
        tomllib.TOMLDecodeError: 配置文件不是有效的 TOML。
        KeyError: ``[llm]``、``active`` 或对应的模型配置不存在。
        pydantic.ValidationError: 当前模型配置缺少必填字段，或字段值不符合
            ``LLMConfig`` 的约束。
    """
    if not path.exists():
        raise FileNotFoundError(f"config.toml not found at {path}.")
    with path.open("rb") as f:
        data = tomllib.load(f)
    llm = data["llm"]
    active = llm["active"]
    group = llm[active]
    return LLMConfig(**group)
