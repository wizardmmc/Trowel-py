"""从 Agent API 错误响应中提取能够直接传给父 Agent 的原因。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


def agent_api_error_detail(response: httpx.Response) -> str | None:
    """返回失败响应中的业务错误说明，成功响应返回 None。

    Args:
        response: Agent API 返回的 HTTP 响应。

    Returns:
        ``detail``、字符串 ``error`` 或 ``error.message``；响应没有结构化说明时
        返回包含状态码的稳定兜底文字。
    """

    if response.is_success:
        return None
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, Mapping):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        error = payload.get("error")
        if isinstance(error, str) and error:
            return error
        if isinstance(error, Mapping):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
    return f"Agent API 请求失败（HTTP {response.status_code}）"
