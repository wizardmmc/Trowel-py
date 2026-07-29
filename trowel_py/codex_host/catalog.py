"""校验 Codex 模型列表并转换为 Agent Host 使用的数据。

模型可用性和思考强度顺序由 Codex 决定，本模块不筛选条目。
"""

from __future__ import annotations

from typing import Any, Mapping

from trowel_py.codex_host.errors import ProtocolViolationError


def parse_model_list_page(
    result: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """校验并转换 Codex ``model/list`` 的一页响应。

    Args:
        result: Codex app-server 返回的 ``model/list`` result 对象。

    Returns:
        转换后的模型条目和下一页游标；没有下一页时游标为 None。

    Raises:
        ProtocolViolationError: 响应结构不符合已录制的 Codex 协议。
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
    """校验并转换一条 Codex 模型记录。

    Args:
        value: ``model/list`` 的 ``data`` 数组中的一项。
        page: 包含该记录的完整 result 对象，用于保留结构错误的诊断上下文。

    Returns:
        使用 Agent Host 字段名的模型记录。

    Raises:
        ProtocolViolationError: 模型记录或其中的思考强度记录不符合已录制的
            Codex 协议。
    """

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
    """校验并转换一条模型思考强度记录。

    Args:
        value: ``supportedReasoningEfforts`` 数组中的一项。
        model: 包含该强度的原始模型记录，用于保留结构错误的诊断上下文。

    Returns:
        Agent Host 使用的思考强度值和说明。

    Raises:
        ProtocolViolationError: 思考强度记录不符合已录制的 Codex 协议。
    """

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
