"""验证模型列表客户端的真实 HTTP shape、候选端点和脱敏错误。"""

from __future__ import annotations

import httpx
import pytest

from trowel_py.configuration.catalog import (
    HttpModelCatalogFetcher,
    build_models_url_candidates,
)
from trowel_py.configuration.errors import ConfigurationError


def test_models_url_candidates_follow_known_compatibility_rules() -> None:
    """版本路径和 Anthropic 兼容后缀按有限规则推导。"""

    assert build_models_url_candidates(
        "https://open.bigmodel.cn/api/coding/paas/v4"
    ) == (
        "https://open.bigmodel.cn/api/coding/paas/v4/models",
        "https://open.bigmodel.cn/api/coding/paas/v4/v1/models",
    )
    assert build_models_url_candidates("https://api.deepseek.com/anthropic") == (
        "https://api.deepseek.com/anthropic/v1/models",
        "https://api.deepseek.com/v1/models",
        "https://api.deepseek.com/models",
    )


@pytest.mark.asyncio
async def test_catalog_fetcher_uses_bearer_token_and_decodes_real_shape() -> None:
    """客户端只向上游发送 secret，并返回排序去重后的公开模型字段。"""

    canary = "catalog-canary-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        """核对真实请求头并返回 OpenAI 风格模型列表。"""

        assert request.headers["authorization"] == f"Bearer {canary}"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "z-model", "owned_by": "provider"},
                    {"id": "a-model"},
                    {"id": "z-model", "owned_by": "latest"},
                ],
            },
        )

    fetcher = HttpModelCatalogFetcher(transport=httpx.MockTransport(handler))
    result = await fetcher.fetch(
        base_url="https://provider.example/v1",
        api_key=canary,
        models_url=None,
    )

    assert tuple(model.id for model in result.models) == ("a-model", "z-model")
    assert canary not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload", "expected_code"),
    [
        (401, {"error": "secret-canary"}, "AUTH_FAILED"),
        (404, {"error": "secret-canary"}, "ENDPOINT_NOT_FOUND"),
        (200, {"models": []}, "UNSUPPORTED_RESPONSE"),
        (200, {"data": []}, "EMPTY_CATALOG"),
        (500, {"error": "secret-canary"}, "NETWORK_ERROR"),
    ],
)
async def test_catalog_failures_use_stable_codes_without_response_body(
    status_code: int,
    payload: dict[str, object],
    expected_code: str,
) -> None:
    """上游正文不进入稳定错误对象。"""

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status_code, json=payload)
    )
    fetcher = HttpModelCatalogFetcher(transport=transport)

    with pytest.raises(ConfigurationError) as raised:
        await fetcher.fetch(
            base_url="https://provider.example/v1",
            api_key="request-secret-canary",
            models_url="https://provider.example/models",
        )

    assert raised.value.code == expected_code
    assert "secret-canary" not in str(raised.value)


@pytest.mark.asyncio
async def test_catalog_timeout_uses_stable_code() -> None:
    """httpx 超时细节不会穿透领域边界。"""

    def timeout(request: httpx.Request) -> httpx.Response:
        """模拟 transport 在发送后读超时。"""

        raise httpx.ReadTimeout("upstream detail", request=request)

    fetcher = HttpModelCatalogFetcher(transport=httpx.MockTransport(timeout))

    with pytest.raises(ConfigurationError) as raised:
        await fetcher.fetch(
            base_url="https://provider.example/v1",
            api_key="request-secret-canary",
            models_url=None,
        )

    assert raised.value.code == "TIMEOUT"
    assert "request-secret-canary" not in str(raised.value)
