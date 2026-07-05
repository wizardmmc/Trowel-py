"""Read cc's model alias config (~/.claude/settings.json env) and expose the
alias → real-model mapping for the /model picker.

slice-027 C2: cc uses fixed aliases (sonnet / opus / haiku, see cc aliases.ts
MODEL_FAMILY_ALIASES) mapped to real models via ANTHROPIC_DEFAULT_<ALIAS>_MODEL
env. trowel reads this mapping so the /model picker shows aliases (which work
across backends — switching backend only edits settings.json, not trowel code)
instead of hardcoding GLM ids. The old hardcoded glm-5.2 was a placeholder.

Aliases are returned in capability order (opus strongest → haiku lightest),
mirroring cc's own ModelPicker ordering. Only aliases present in env are
returned, so a backend that maps a subset shows just those.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# cc's MODEL_FAMILY_ALIASES, ordered strongest → lightest. We surface them in
# this fixed order; only ones present in env are returned.
_ALIAS_ORDER: tuple[str, ...] = ("opus", "sonnet", "haiku")

# trowel-side display copy per alias — the picker subtitle describing when to
# pick each one. Backend-agnostic (about capability, not the real model id).
_ALIAS_DESCRIPTION: dict[str, str] = {
    "opus": "最强推理，长上下文任务",
    "sonnet": "平衡的代码模型，日常主力",
    "haiku": "快速轻量，简单改动 / 批量任务",
}


@dataclass(frozen=True)
class ModelOption:
    """One row of GET /cc/models.

    Attributes:
        value: the cc alias to pass to --model / /model (e.g. 'opus'). This is
            what RestartSession stores in self._model and launcher puts after
            --model.
        label: human-friendly display name (e.g. 'Opus').
        real_model: the underlying model id from settings.json env (e.g.
            'glm-5.2[1M]'); the picker shows it mono-spaced so the user sees
            what the alias resolves to on the current backend.
        description: trowel-side subtitle describing when to pick this alias.
    """

    value: str
    label: str
    real_model: str
    description: str


def _settings_path() -> Path:
    """Return cc's settings.json path (~/.claude/settings.json)."""
    return Path.home() / ".claude" / "settings.json"


def _load_env(path: Path) -> dict[str, str]:
    """Read the 'env' block from a cc settings.json; missing/invalid → empty.

    cc's settings.json has a top-level 'env' object whose keys are exported as
    environment variables to cc's subprocess. We only care about the
    ANTHROPIC_DEFAULT_*_MODEL entries; non-string values are dropped.
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
    """List available model aliases by reading cc's settings.json env.

    For each alias in _ALIAS_ORDER, look for ANTHROPIC_DEFAULT_<ALIAS>_MODEL in
    the env block. Only aliases present are returned (a backend that maps only
    sonnet+haiku shows just those — no guessing).

    Args:
        settings_path: override (tests inject a fixture); defaults to
            ~/.claude/settings.json.

    Returns:
        alias rows in capability order (opus → sonnet → haiku).
    """
    path = settings_path or _settings_path()
    env = _load_env(path)
    out: list[ModelOption] = []
    for alias in _ALIAS_ORDER:
        real = env.get(f"ANTHROPIC_DEFAULT_{alias.upper()}_MODEL")
        if real:
            out.append(
                ModelOption(
                    value=alias,
                    label=alias.capitalize(),
                    real_model=real,
                    description=_ALIAS_DESCRIPTION.get(alias, ""),
                )
            )
    return out
