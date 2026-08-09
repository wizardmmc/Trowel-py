"""发送 GLM 额度 HTTP 请求，并把网络异常和响应体转换为额度层格式。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

import httpx


_ResponseT = TypeVar("_ResponseT")


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    *,
    timeout: float,
    http_error_type: type[BaseException],
    network_error_type: type[BaseException],
    response_type: Callable[..., _ResponseT],
    mapping_type: type[Any],
) -> _ResponseT:
    """发送 GLM 额度请求，并用调用方提供的类型构造返回值。

    只有 ``client.get`` 抛出的 ``http_error_type`` 会转换为
    ``network_error_type``，原异常保存在 ``__cause__``。``response.json()``
    抛出 ``ValueError`` 或返回非 ``mapping_type`` 值时，传给
    ``response_type`` 的响应体为 None；请求和解析过程中的其他异常继续抛出。

    Args:
        client: 发送额度请求的异步 HTTP 客户端。
        url: GLM 额度接口的完整 URL。
        headers: 随请求发送给额度接口的请求头。
        timeout: 传给 ``client.get`` 的超时秒数，不是整次请求的总时限。
        http_error_type: 只在 ``client.get`` 调用处捕获并转换的异常类型。
        network_error_type: 用捕获异常的消息构造并抛出的异常类型。
        response_type: 接收 HTTP 状态码和处理后响应体、构造返回值的可调用对象。
        mapping_type: 判断 JSON 解析结果能否作为对象响应体的运行时类型。

    Returns:
        ``response_type`` 构造的值；``response.json()`` 抛出 ``ValueError`` 或
        返回非对象值时，传入的响应体为 None。
    """

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
