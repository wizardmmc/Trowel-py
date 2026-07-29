"""从 Claude Code settings env 读取 ``/model`` alias 到真实模型的映射。

上游 ``MODEL_FAMILY_ALIASES`` 固定为 opus、sonnet、haiku；settings 通过
``ANTHROPIC_DEFAULT_<ALIAS>_MODEL`` 绑定后端模型。这里只返回已配置 alias，
并保持上游能力顺序。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 与上游 ModelPicker 的能力顺序一致。
_ALIAS_ORDER: tuple[str, ...] = ("opus", "sonnet", "haiku")

_ALIAS_DESCRIPTION: dict[str, str] = {
    "opus": "最强推理，长上下文任务",
    "sonnet": "平衡的代码模型，日常主力",
    "haiku": "快速轻量，简单改动 / 批量任务",
}


@dataclass(frozen=True)
class ModelOption:
    """表示 ``GET /api/cc/models`` 返回的一项 Claude Code 模型选择。

    Attributes:
        value: 传给 ``/model`` 和 ``--model`` 的 alias。
        label: 前端展示的首字母大写 alias。
        real_model: settings env 为该 alias 绑定的后端模型名。
        description: 前端展示的固定中文能力说明。
        is_default: 该 alias 是否是当前配置推导出的默认选择。
    """

    value: str
    label: str
    real_model: str
    description: str
    is_default: bool = False


def _settings_path() -> Path:
    """返回当前用户的 Claude Code settings 文件路径。"""

    return Path.home() / ".claude" / "settings.json"


def _load_env(path: Path) -> dict[str, str]:
    """读取 Claude Code settings 中值为字符串的 env 条目。

    Args:
        path: 要读取的 settings JSON 文件。

    Returns:
        settings 顶层 ``env`` 中的字符串键值；文件不存在、无法读取、JSON 无效
        或 ``env`` 不是对象时为空字典。非字符串值会被忽略。

    Raises:
        UnicodeDecodeError: settings 文件不是有效的 UTF-8 文本。
        AttributeError: settings 顶层 JSON 不是对象。
    """

    if not path.is_file():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    env = data.get("env")
    if not isinstance(env, dict):
        return {}
    return {k: v for k, v in env.items() if isinstance(v, str)}


def list_models(settings_path: Path | None = None) -> list[ModelOption]:
    """按 opus、sonnet、haiku 的顺序列出已配置的模型 alias。

    ``ANTHROPIC_MODEL`` 与某个已配置后端模型相同时，顺序中的第一个匹配 alias
    设为默认项；没有匹配时优先回退到 sonnet，否则使用第一个已配置 alias。

    Args:
        settings_path: 要读取的 Claude Code settings 文件；None 表示使用当前用户
            的默认路径。

    Returns:
        已配置且值非空的模型选项；没有可用 alias 时为空列表。

    Raises:
        UnicodeDecodeError: settings 文件不是有效的 UTF-8 文本。
        AttributeError: settings 顶层 JSON 不是对象。
    """

    path = settings_path or _settings_path()
    env = _load_env(path)
    reals: dict[str, str] = {}
    for alias in _ALIAS_ORDER:
        real = env.get(f"ANTHROPIC_DEFAULT_{alias.upper()}_MODEL")
        if real:
            reals[alias] = real

    anthropic_model = env.get("ANTHROPIC_MODEL")
    default_alias: str | None = None
    if anthropic_model:
        default_alias = next(
            (a for a, real in reals.items() if real == anthropic_model), None
        )
    if default_alias is None:
        default_alias = "sonnet" if "sonnet" in reals else next(iter(reals), None)

    out: list[ModelOption] = []
    for alias in _ALIAS_ORDER:
        real = reals.get(alias)
        if not real:
            continue
        out.append(
            ModelOption(
                value=alias,
                label=alias.capitalize(),
                real_model=real,
                description=_ALIAS_DESCRIPTION.get(alias, ""),
                is_default=(alias == default_alias),
            )
        )
    return out
