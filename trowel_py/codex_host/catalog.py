"""Codex ``model/list`` parsing for the public Agent catalog.

The app-server owns model availability and effort ordering. This module only
validates the 0.144.0 wire shape and renames camelCase fields for trowel's
public API; it never filters model ids or reasoning-effort values.
"""

from __future__ import annotations

from typing import Any, Mapping

from trowel_py.codex_host.errors import ProtocolViolationError


def parse_model_list_page(
    result: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Validate and normalize one native ``model/list`` response page.

    Args:
        result: JSON-RPC result object returned by Codex app-server 0.144.0.

    Returns:
        A pair of normalized model rows and the opaque next-page cursor.

    Raises:
        ProtocolViolationError: If the result no longer matches the recorded
            app-server shape.
    """

    raw_rows = result.get("data")
    if not isinstance(raw_rows, list):
        raise ProtocolViolationError(
            "model/list response data is not an array", payload=dict(result)
        )
    rows = [_parse_model_row(row, result) for row in raw_rows]
    cursor = result.get("nextCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ProtocolViolationError(
            "model/list nextCursor is not a string or null", payload=dict(result)
        )
    return rows, cursor


def _parse_model_row(value: object, page: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one model row without imposing a model whitelist."""

    if not isinstance(value, Mapping):
        raise ProtocolViolationError(
            "model/list row is not an object", payload=dict(page)
        )
    required_strings = (
        "id",
        "model",
        "displayName",
        "description",
        "defaultReasoningEffort",
    )
    if any(not isinstance(value.get(key), str) for key in required_strings):
        raise ProtocolViolationError(
            "model/list row is missing a required string", payload=dict(value)
        )
    if not isinstance(value.get("isDefault"), bool):
        raise ProtocolViolationError(
            "model/list row is missing native isDefault", payload=dict(value)
        )
    raw_efforts = value.get("supportedReasoningEfforts")
    if not isinstance(raw_efforts, list):
        raise ProtocolViolationError(
            "model/list supportedReasoningEfforts is not an array",
            payload=dict(value),
        )
    efforts = [_parse_effort_row(option, value) for option in raw_efforts]
    return {
        "id": value["id"],
        "model": value["model"],
        "display_name": value["displayName"],
        "description": value["description"],
        "is_default": value["isDefault"],
        "default_effort": value["defaultReasoningEffort"],
        "supported_efforts": efforts,
    }


def _parse_effort_row(value: object, model: Mapping[str, Any]) -> dict[str, str]:
    """Normalize one reasoning effort while preserving its native value."""

    if not isinstance(value, Mapping):
        raise ProtocolViolationError(
            "model/list effort row is not an object", payload=dict(model)
        )
    effort = value.get("reasoningEffort")
    description = value.get("description")
    if not isinstance(effort, str) or not isinstance(description, str):
        raise ProtocolViolationError(
            "model/list effort row is missing native value/description",
            payload=dict(value),
        )
    return {"value": effort, "description": description}
