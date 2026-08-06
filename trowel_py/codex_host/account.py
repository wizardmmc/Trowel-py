"""校验并压缩 Codex app-server 的原生账号响应。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trowel_py.codex_host.errors import ProtocolViolationError


def parse_account_read(result: Mapping[str, Any]) -> dict[str, str | None]:
    """把 ``account/read`` 转成不含 token 的账号摘要。

    Args:
        result: Codex 0.144.0 ``account/read`` 的 JSON-RPC result。

    Returns:
        登录状态、认证模式、邮箱和套餐；未登录时后三项为空。

    Raises:
        ProtocolViolationError: 上游响应不符合已验证协议。
    """

    account = result.get("account")
    if account is None:
        return {
            "status": "not_logged_in",
            "auth_mode": None,
            "email": None,
            "plan_type": None,
        }
    if not isinstance(account, Mapping):
        raise ProtocolViolationError(
            "account/read account is not an object or null", payload=dict(result)
        )
    auth_mode = account.get("type")
    if not isinstance(auth_mode, str):
        raise ProtocolViolationError(
            "account/read account is missing type", payload=dict(account)
        )
    email = account.get("email")
    plan_type = account.get("planType")
    if email is not None and not isinstance(email, str):
        raise ProtocolViolationError(
            "account/read email is not a string or null", payload=dict(account)
        )
    if plan_type is not None and not isinstance(plan_type, str):
        raise ProtocolViolationError(
            "account/read planType is not a string or null", payload=dict(account)
        )
    return {
        "status": "logged_in" if auth_mode == "chatgpt" else "unsupported",
        "auth_mode": auth_mode,
        "email": email,
        "plan_type": plan_type,
    }


def parse_device_code_login(result: Mapping[str, Any]) -> dict[str, str]:
    """校验 ``account/login/start`` 的 ChatGPT device-code 响应。

    Args:
        result: Codex 0.144.0 ``chatgptDeviceCode`` 登录启动结果。

    Returns:
        前端展示和轮询需要的登录 ID、验证地址与用户码。

    Raises:
        ProtocolViolationError: 上游响应缺少必需字段或登录类型漂移。
    """

    if result.get("type") != "chatgptDeviceCode":
        raise ProtocolViolationError(
            "account/login/start returned an unexpected type", payload=dict(result)
        )
    fields = {
        "login_id": result.get("loginId"),
        "verification_url": result.get("verificationUrl"),
        "user_code": result.get("userCode"),
    }
    if any(not isinstance(value, str) or not value for value in fields.values()):
        raise ProtocolViolationError(
            "account/login/start is missing device-code fields", payload=dict(result)
        )
    return {key: str(value) for key, value in fields.items()}
