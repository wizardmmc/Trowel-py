"""GLM quota client fault->status mapping (slice-093-pre, criterion 2).

Every failure mode the undocumented endpoint can produce maps to a non-ok
status and the client NEVER raises out of ``fetch``. The fetcher is injected,
so no network is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.quota.glm import FetchResponse, GlmQuotaClient, NetworkError
from trowel_py.quota.types import QuotaStatus

FIXTURES = Path(__file__).parent / "fixtures"
_REAL_PATH = FIXTURES / "glm_quota_real.json"
pytestmark = pytest.mark.skipif(
    not _REAL_PATH.exists(),
    reason="local GLM recording is gitignored; not present on a fresh clone",
)


def _real() -> dict:
    return json.loads(_REAL_PATH.read_text(encoding="utf-8"))


NOW = 1_700_000_000_000


def _client(queue: list, seen: list | None = None) -> GlmQuotaClient:
    """Build a client whose injected fetcher returns queued responses.

    A queued ``BaseException`` is raised (simulating a network failure).
    """

    async def fetcher(url: str, headers):
        if seen is not None:
            seen.append(dict(headers))
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return GlmQuotaClient(
        "https://open.bigmodel.cn", fetcher=fetcher, now_ms=lambda: NOW
    )


async def test_200_valid_body_ok_and_raw_key_auth() -> None:
    """Success -> ok; auth header is the RAW key with no Bearer prefix."""

    seen: list = []
    client = _client([FetchResponse(200, _real())], seen=seen)
    snap = await client.fetch("glm-a", "secret.key")
    assert snap.status is QuotaStatus.OK
    assert seen[0]["Authorization"] == "secret.key"


async def test_http_401_is_auth_error() -> None:
    snap = await _client([FetchResponse(401, None)]).fetch("a", "k")
    assert snap.status is QuotaStatus.AUTH_ERROR


async def test_business_code_401_is_auth_error() -> None:
    body = {"code": 401, "msg": "Credential expired", "success": False}
    snap = await _client([FetchResponse(200, body)]).fetch("a", "k")
    assert snap.status is QuotaStatus.AUTH_ERROR


async def test_business_code_1001_is_auth_error() -> None:
    body = {"code": 1001, "msg": "no auth header", "success": False}
    snap = await _client([FetchResponse(200, body)]).fetch("a", "k")
    assert snap.status is QuotaStatus.AUTH_ERROR


async def test_business_code_500_is_server_error() -> None:
    body = {"code": 500, "msg": "Internal Error", "success": False}
    snap = await _client([FetchResponse(200, body)]).fetch("a", "k")
    assert snap.status is QuotaStatus.SERVER_ERROR


async def test_http_500_is_server_error() -> None:
    snap = await _client([FetchResponse(500, None)]).fetch("a", "k")
    assert snap.status is QuotaStatus.SERVER_ERROR


async def test_network_exception_is_network_error() -> None:
    snap = await _client([NetworkError("timeout")]).fetch("a", "k")
    assert snap.status is QuotaStatus.NETWORK_ERROR


async def test_unparseable_200_body_is_server_error() -> None:
    snap = await _client([FetchResponse(200, None)]).fetch("a", "k")
    assert snap.status is QuotaStatus.SERVER_ERROR
