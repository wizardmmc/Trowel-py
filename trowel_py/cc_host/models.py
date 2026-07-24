"""从 CC settings env 读取 `/model` alias 到真实 model 的映射。

上游 `MODEL_FAMILY_ALIASES` 固定为 opus、sonnet、haiku；settings 通过
`ANTHROPIC_DEFAULT_<ALIAS>_MODEL` 绑定后端 model。这里只返回已配置 alias，
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
    """`/cc/models` 的 alias、真实 model、显示文案与默认标记。"""

    value: str
    label: str
    real_model: str
    description: str
    is_default: bool = False


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _load_env(path: Path) -> dict[str, str]:
    """读取 settings env；缺失、损坏或非字符串值按空配置处理。"""
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
    """按能力顺序列出 alias；默认项优先匹配 ANTHROPIC_MODEL，再回退 sonnet。"""
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
