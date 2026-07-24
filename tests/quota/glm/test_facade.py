from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

from trowel_py.quota import glm
from trowel_py.quota.glm import (
    FetchResponse,
    GlmQuotaClient,
    NetworkError,
)


_ORIGINAL_SYMBOLS = frozenset(
    """
    Any AsyncFetcher Awaitable Callable FetchResponse GlmQuotaClient Mapping
    NetworkError Provider QUOTA_PATH QuotaSnapshot QuotaStatus QuotaWindow
    QuotaWindowKind _AUTH_BUSINESS_CODES _SESSION_UNIT _WEEKLY_UNIT _as_float
    _as_int _default_now_ms _extract_limits _find_limit _map_response _snapshot
    _window annotations dataclass httpx httpx_fetcher parse_glm_quota time
    """.split()
)


def test_facade_keeps_original_contracts() -> None:
    assert _ORIGINAL_SYMBOLS <= vars(glm).keys()
    expected_signatures: dict[Callable[..., Any], str] = {
        FetchResponse: ("(status: 'int', body: 'Mapping[str, Any] | None') -> None"),
        GlmQuotaClient: (
            "(host: 'str' = 'https://open.bigmodel.cn', *, "
            "fetcher: 'AsyncFetcher', now_ms: 'Callable[[], int] | None' = "
            "None, timeout: 'float' = 10.0) -> 'None'"
        ),
        glm.httpx_fetcher: (
            "(client: 'httpx.AsyncClient', *, timeout: 'float' = 10.0) "
            "-> 'AsyncFetcher'"
        ),
        glm._as_float: "(value: 'Any') -> 'float | None'",
        glm._as_int: "(value: 'Any') -> 'int | None'",
        glm._find_limit: (
            "(limits: 'list[Mapping[str, Any]]', type_: 'str', "
            "unit: 'int | None') -> 'Mapping[str, Any] | None'"
        ),
        glm._window: (
            "(kind: 'QuotaWindowKind', limit: 'Mapping[str, Any] | None') "
            "-> 'QuotaWindow | None'"
        ),
        glm._extract_limits: (
            "(raw: 'Mapping[str, Any]') -> "
            "'tuple[list[Mapping[str, Any]], Mapping[str, Any]]'"
        ),
        glm.parse_glm_quota: (
            "(raw: 'Mapping[str, Any]', *, account_id: 'str', "
            "fetched_at: 'int') -> 'QuotaSnapshot'"
        ),
        glm._snapshot: (
            "(account_id: 'str', fetched_at: 'int', status: 'QuotaStatus') "
            "-> 'QuotaSnapshot'"
        ),
        glm._map_response: (
            "(resp: 'FetchResponse', *, account_id: 'str', "
            "fetched_at: 'int') -> 'QuotaSnapshot'"
        ),
        glm._default_now_ms: "() -> 'int'",
        GlmQuotaClient.fetch: (
            "(self, account_id: 'str', api_key: 'str') -> 'QuotaSnapshot'"
        ),
    }
    assert {
        function: str(inspect.signature(function)) for function in expected_signatures
    } == expected_signatures
    assert {function.__module__ for function in expected_signatures} == {
        "trowel_py.quota.glm"
    }
    assert NetworkError.__module__ == "trowel_py.quota.glm"
    assert [field.name for field in fields(FetchResponse)] == [
        "status",
        "body",
    ]
    assert FetchResponse.__match_args__ == ("status", "body")
    with pytest.raises(FrozenInstanceError):
        setattr(FetchResponse(200, None), "status", 500)


async def test_transport_facade_uses_current_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HttpFailure(Exception):
        pass

    class NetworkFailure(Exception):
        pass

    response_type = object()
    mapping_type = object()
    captured: dict[str, Any] = {}
    sentinel = object()

    async def run(
        client: Any,
        url: str,
        headers: Any,
        **values: Any,
    ) -> Any:
        captured.update(
            client=client,
            url=url,
            headers=headers,
            **values,
        )
        return sentinel

    client = object()
    fetch = glm.httpx_fetcher(
        cast(Any, client),
        timeout=2.5,
    )
    monkeypatch.setattr(
        glm,
        "httpx",
        SimpleNamespace(HTTPError=HttpFailure),
    )
    monkeypatch.setattr(glm, "NetworkError", NetworkFailure)
    monkeypatch.setattr(glm, "FetchResponse", response_type)
    monkeypatch.setattr(glm, "Mapping", mapping_type)
    monkeypatch.setattr(glm, "_run_httpx_fetch", run)

    headers = {"X": "y"}
    assert await fetch("url", headers) is sentinel
    assert captured == {
        "client": client,
        "url": "url",
        "headers": headers,
        "timeout": 2.5,
        "http_error_type": HttpFailure,
        "network_error_type": NetworkFailure,
        "response_type": response_type,
        "mapping_type": mapping_type,
    }


def test_parser_facade_uses_current_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinels = {
        name: object()
        for name in (
            "extract",
            "find",
            "window",
            "snapshot",
            "snapshot_type",
            "provider",
            "ok",
            "no_data",
            "session",
            "weekly",
            "monthly",
            "mapping",
        )
    }
    captured: dict[str, Any] = {}
    expected = object()

    def run(raw: Any, **values: Any) -> Any:
        captured.update(raw=raw, **values)
        return expected

    monkeypatch.setattr(glm, "_extract_limits", sentinels["extract"])
    monkeypatch.setattr(glm, "_find_limit", sentinels["find"])
    monkeypatch.setattr(glm, "_window", sentinels["window"])
    monkeypatch.setattr(glm, "_snapshot", sentinels["snapshot"])
    monkeypatch.setattr(glm, "QuotaSnapshot", sentinels["snapshot_type"])
    monkeypatch.setattr(
        glm,
        "Provider",
        SimpleNamespace(GLM=sentinels["provider"]),
    )
    monkeypatch.setattr(
        glm,
        "QuotaStatus",
        SimpleNamespace(
            OK=sentinels["ok"],
            NO_DATA=sentinels["no_data"],
        ),
    )
    monkeypatch.setattr(
        glm,
        "QuotaWindowKind",
        SimpleNamespace(
            SESSION_5H=sentinels["session"],
            WEEKLY=sentinels["weekly"],
            WEB_SEARCHES_MONTHLY=sentinels["monthly"],
        ),
    )
    monkeypatch.setattr(glm, "_SESSION_UNIT", 13)
    monkeypatch.setattr(glm, "_WEEKLY_UNIT", 16)
    monkeypatch.setattr(glm, "Mapping", sentinels["mapping"])
    monkeypatch.setattr(glm, "_run_parse_quota", run)
    raw = {"payload": True}

    assert (
        glm.parse_glm_quota(
            raw,
            account_id="account",
            fetched_at=7,
        )
        is expected
    )
    assert captured == {
        "raw": raw,
        "account_id": "account",
        "fetched_at": 7,
        "extract_limits": sentinels["extract"],
        "find_limit": sentinels["find"],
        "build_window": sentinels["window"],
        "snapshot_without_windows": sentinels["snapshot"],
        "snapshot_type": sentinels["snapshot_type"],
        "provider": sentinels["provider"],
        "ok_status": sentinels["ok"],
        "no_data_status": sentinels["no_data"],
        "session_kind": sentinels["session"],
        "weekly_kind": sentinels["weekly"],
        "monthly_kind": sentinels["monthly"],
        "session_unit": 13,
        "weekly_unit": 16,
        "mapping_type": sentinels["mapping"],
    }


def test_window_facade_uses_current_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    float_reader = object()
    int_reader = object()
    window_type = object()
    monthly_kind = object()
    mapping_type = object()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(glm, "_as_float", float_reader)
    monkeypatch.setattr(glm, "_as_int", int_reader)
    monkeypatch.setattr(glm, "QuotaWindow", window_type)
    monkeypatch.setattr(
        glm,
        "QuotaWindowKind",
        SimpleNamespace(WEB_SEARCHES_MONTHLY=monthly_kind),
    )
    monkeypatch.setattr(
        glm,
        "_run_window",
        lambda kind, limit, **values: captured.update(
            kind=kind,
            limit=limit,
            **values,
        ),
    )
    monkeypatch.setattr(glm, "Mapping", mapping_type)
    monkeypatch.setattr(
        glm,
        "_run_extract_limits",
        lambda raw, **values: captured.update(raw=raw, **values) or ([], {}),
    )

    glm._window(cast(Any, monthly_kind), {"percentage": 1})
    glm._extract_limits({"data": {}})
    assert captured == {
        "kind": monthly_kind,
        "limit": {"percentage": 1},
        "as_float": float_reader,
        "as_int": int_reader,
        "window_type": window_type,
        "monthly_kind": monthly_kind,
        "raw": {"data": {}},
        "mapping_type": mapping_type,
    }
