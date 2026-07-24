from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.quota.glm.support import (
    NOW,
    client_with_queue,
    ok_snapshot,
)
from trowel_py.quota import glm
from trowel_py.quota.glm import (
    FetchResponse,
    GlmQuotaClient,
    NetworkError,
)
from trowel_py.quota.types import QuotaStatus


async def test_success_uses_raw_key_and_current_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = ok_snapshot("account")
    calls: list[tuple[dict, str, int]] = []

    def parse(
        body: dict,
        *,
        account_id: str,
        fetched_at: int,
    ):
        calls.append((body, account_id, fetched_at))
        return expected

    monkeypatch.setattr(glm, "parse_glm_quota", parse)
    seen: list[tuple[str, dict[str, str]]] = []
    client = client_with_queue(
        [FetchResponse(200, {"code": 200})],
        seen,
    )

    assert await client.fetch("account", "synthetic-key") is expected
    assert seen == [
        (
            "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
            {
                "Authorization": "synthetic-key",
                "Accept": "application/json",
            },
        )
    ]
    assert calls == [({"code": 200}, "account", NOW)]


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_http_status_is_auth_error(status: int) -> None:
    snapshot = await client_with_queue([FetchResponse(status, None)]).fetch("a", "k")
    assert snapshot.status is QuotaStatus.AUTH_ERROR


@pytest.mark.parametrize("code", [401, 1001])
async def test_auth_business_code_is_auth_error(code: int) -> None:
    snapshot = await client_with_queue([FetchResponse(200, {"code": code})]).fetch(
        "a", "k"
    )
    assert snapshot.status is QuotaStatus.AUTH_ERROR


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (199, None),
        (500, None),
        (200, None),
        (200, {"code": 500}),
    ],
)
async def test_invalid_response_is_server_error(
    status: int,
    body: dict | None,
) -> None:
    snapshot = await client_with_queue([FetchResponse(status, body)]).fetch("a", "k")
    assert snapshot.status is QuotaStatus.SERVER_ERROR


async def test_network_exception_is_network_error() -> None:
    snapshot = await client_with_queue([NetworkError("timeout")]).fetch("a", "k")
    assert snapshot.status is QuotaStatus.NETWORK_ERROR


async def test_unexpected_fetcher_exception_still_raises() -> None:
    with pytest.raises(RuntimeError, match="unexpected"):
        await client_with_queue([RuntimeError("unexpected")]).fetch("a", "k")


def test_response_mapper_uses_current_facade_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_status = object()
    server_status = object()
    snapshots: list[tuple[str, int, Any]] = []
    monkeypatch.setattr(glm, "_AUTH_BUSINESS_CODES", {777})
    monkeypatch.setattr(
        glm,
        "QuotaStatus",
        SimpleNamespace(
            AUTH_ERROR=auth_status,
            SERVER_ERROR=server_status,
        ),
    )

    def snapshot(account: str, fetched: int, status: Any) -> tuple[str, Any]:
        snapshots.append((account, fetched, status))
        return ("snapshot", status)

    monkeypatch.setattr(glm, "_snapshot", snapshot)
    assert glm._map_response(
        FetchResponse(200, {"code": 777}),
        account_id="a",
        fetched_at=3,
    ) == ("snapshot", auth_status)
    assert snapshots == [("a", 3, auth_status)]

    parsed = object()
    monkeypatch.setattr(
        glm,
        "parse_glm_quota",
        lambda body, *, account_id, fetched_at: parsed,
    )
    assert (
        glm._map_response(
            FetchResponse(200, {"code": 200}),
            account_id="a",
            fetched_at=3,
        )
        is parsed
    )


async def test_client_uses_current_clock_path_and_mappers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    async def fetcher(url: str, headers) -> FetchResponse:
        seen.append((url, dict(headers)))
        return FetchResponse(200, {})

    expected = object()
    monkeypatch.setattr(glm, "_default_now_ms", lambda: 123)
    monkeypatch.setattr(glm, "QUOTA_PATH", "/custom")
    monkeypatch.setattr(
        glm,
        "_map_response",
        lambda response, *, account_id, fetched_at: expected,
    )
    client = GlmQuotaClient(
        "https://provider.test/",
        fetcher=fetcher,
        timeout=4.5,
    )

    assert client._timeout == 4.5
    assert await client.fetch("account", "key") is expected
    assert seen == [
        (
            "https://provider.test/custom",
            {
                "Authorization": "key",
                "Accept": "application/json",
            },
        )
    ]
