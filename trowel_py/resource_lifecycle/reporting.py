"""让 Trowel 自有子进程在对外服务前回报可核验的进程身份。"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

_URL_ENV = "TROWEL_RESOURCE_REGISTRATION_URL"
_CREDENTIAL_ENV = "TROWEL_RESOURCE_REGISTRATION_CREDENTIAL"
_TOKEN_ENV = "TROWEL_RESOURCE_REGISTRATION_TOKEN"


async def report_current_process(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """用预签发令牌向当前 sidecar 登记 PID；未配置桌面登记时直接跳过。

    Args:
        environment: 子进程启动环境；None 时读取当前进程环境。完整配置必须同时
            包含私有端点、桌面凭据和 owner 令牌。

    Returns:
        完成登记时为 True；browser 模式没有登记环境时为 False。

    Raises:
        RuntimeError: 只配置了部分登记环境，或 sidecar 拒绝登记。
    """

    env = environment if environment is not None else os.environ
    url = env.get(_URL_ENV, "").strip()
    credential = env.get(_CREDENTIAL_ENV, "").strip()
    token = env.get(_TOKEN_ENV, "").strip()
    configured = (url, credential, token)
    if not any(configured):
        return False
    if not all(configured):
        raise RuntimeError("incomplete resource registration environment")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {credential}"},
                json={"token": token, "pid": os.getpid()},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"resource registration failed: {type(exc).__name__}"
        ) from exc
    return True
