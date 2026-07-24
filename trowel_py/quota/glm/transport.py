"""GLM quota 的 httpx transport 适配。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    *,
    timeout: float,
    http_error_type: type[BaseException],
    network_error_type: type[BaseException],
    response_type: Callable[..., Any],
    mapping_type: type[Any],
) -> Any:
    try:
        response = await client.get(
            url,
            headers=dict(headers),
            timeout=timeout,
        )
    except http_error_type as exc:
        raise network_error_type(str(exc)) from exc
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    body = parsed if isinstance(parsed, mapping_type) else None
    return response_type(response.status_code, body)
