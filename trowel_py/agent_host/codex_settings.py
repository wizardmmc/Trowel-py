"""选择下一个 Codex turn 使用的模型与 reasoning effort。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class UnknownModelError(ValueError):
    """所选模型不在原生 catalog 中。"""


class NoUsableEffortError(ValueError):
    """所选模型没有可用的请求值或默认 effort。"""


@dataclass(frozen=True)
class TurnSettings:
    model: str
    effort: str
    adjusted: bool


def select_turn_settings(
    catalog: Sequence[Mapping[str, Any]],
    *,
    requested_model: str | None,
    stored_model: str | None,
    native_model: str | None,
    configured_model: str | None,
    requested_effort: str | None,
    stored_effort: str | None,
    native_effort: str | None,
    configured_effort: str | None,
) -> TurnSettings:
    """依次选择请求、存储、原生、配置值；不支持的 effort 回落到模型原生默认值。"""

    default_row = next(
        (item for item in catalog if item.get("is_default") is True),
        catalog[0] if catalog else None,
    )
    current_model = (
        requested_model
        or stored_model
        or native_model
        or configured_model
        or (default_row.get("id") if default_row is not None else None)
    )
    row = next(
        (
            item
            for item in catalog
            if item.get("id") == current_model or item.get("model") == current_model
        ),
        None,
    )
    if row is None:
        raise UnknownModelError(f"model {current_model!r} is not in the native catalog")

    supported = [
        str(item["value"])
        for item in row.get("supported_efforts", [])
        if isinstance(item, dict) and isinstance(item.get("value"), str)
    ]
    selected_request = (
        requested_effort
        or stored_effort
        or native_effort
        or configured_effort
        or row.get("default_effort")
    )
    adjusted = selected_request not in supported
    selected_effort = str(row["default_effort"]) if adjusted else str(selected_request)
    if selected_effort not in supported:
        raise NoUsableEffortError(
            f"model {current_model!r} has no usable default effort"
        )
    return TurnSettings(
        model=str(row["id"]),
        effort=selected_effort,
        adjusted=adjusted,
    )
