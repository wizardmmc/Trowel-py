from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

from trowel_py.quota.glm import (
    FetchResponse,
    NetworkError,
    httpx_fetcher,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any,
    ) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(
        self,
        result: FakeResponse | BaseException,
    ) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, str], float]] = []

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append((url, headers, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


async def test_transport_forwards_request_and_parses_object_body() -> None:
    client = FakeClient(FakeResponse(202, {"code": 200}))
    fetch = httpx_fetcher(
        cast(httpx.AsyncClient, client),
        timeout=3.5,
    )

    assert await fetch("https://provider.test/quota", {"X": "y"}) == (
        FetchResponse(202, {"code": 200})
    )
    assert client.calls == [("https://provider.test/quota", {"X": "y"}, 3.5)]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "text",
        ValueError("invalid json"),
    ],
)
async def test_transport_maps_non_object_json_to_none(
    payload: Any,
) -> None:
    fetch = httpx_fetcher(
        cast(httpx.AsyncClient, FakeClient(FakeResponse(200, payload)))
    )
    assert await fetch("url", {}) == FetchResponse(200, None)


async def test_transport_normalizes_httpx_error_with_cause() -> None:
    source = httpx.ReadTimeout("timed out")
    fetch = httpx_fetcher(cast(httpx.AsyncClient, FakeClient(source)))

    with pytest.raises(NetworkError, match="timed out") as raised:
        await fetch("url", {})
    assert raised.value.__cause__ is source


@pytest.mark.parametrize(
    "source",
    [
        RuntimeError("request failed"),
        TypeError("decode failed"),
    ],
)
async def test_transport_preserves_unexpected_errors(
    source: Exception,
) -> None:
    result: FakeResponse | BaseException
    if isinstance(source, RuntimeError):
        result = source
    else:
        result = FakeResponse(200, source)
    fetch = httpx_fetcher(cast(httpx.AsyncClient, FakeClient(result)))

    with pytest.raises(type(source), match=str(source)):
        await fetch("url", {})
