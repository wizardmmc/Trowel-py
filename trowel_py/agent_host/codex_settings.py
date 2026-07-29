"""为 Codex 的下一轮交互选择可用的模型和思考强度。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class UnknownModelError(ValueError):
    """所选模型不在 Codex 提供的模型列表中。"""


class NoUsableEffortError(ValueError):
    """所选模型没有可用的思考强度设置。"""


@dataclass(frozen=True)
class TurnSettings:
    """保存下一轮 Codex 交互最终采用的模型和思考强度。

    Attributes:
        model: 最终采用的 Codex 模型 ID。
        effort: 最终采用的思考强度。
        adjusted: 是否因原选择不可用而改用了模型的默认思考强度。
    """

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
    """按来源优先级选择下一轮 Codex 使用的模型和思考强度。

    优先使用本次请求，其次使用持久化记录、当前 Codex 会话和启动配置。没有指定
    模型时使用模型列表中的默认项；思考强度不可用时改用该模型的默认值。

    Args:
        catalog: Codex 提供的模型及其可用思考强度列表。
        requested_model: 本次请求明确指定的模型。
        stored_model: 会话绑定中保存的模型。
        native_model: 当前 Codex 会话实际使用的模型。
        configured_model: 创建 Codex 会话时配置的模型。
        requested_effort: 本次请求明确指定的思考强度。
        stored_effort: 会话绑定中保存的思考强度。
        native_effort: 当前 Codex 会话实际使用的思考强度。
        configured_effort: 创建 Codex 会话时配置的思考强度。

    Returns:
        最终可用的模型、思考强度及是否发生调整。

    Raises:
        UnknownModelError: 最终选出的模型不在 Codex 模型列表中。
        NoUsableEffortError: 所选模型没有可用的思考强度。
    """

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
