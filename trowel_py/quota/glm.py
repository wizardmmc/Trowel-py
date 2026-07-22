"""GLM Coding Plan quota source (slice-093-pre).

Two layers:
- ``parse_glm_quota`` — a pure mapper from the undocumented
  ``GET https://open.bigmodel.cn/api/monitor/usage/quota/limit`` response to a
  ``QuotaSnapshot``. Tested against the real recorded response.
- ``GlmQuotaClient`` — the HTTP fetch with injectable transport; maps every
  failure to a non-ok status and never raises out of ``fetch``.

Contract (see memory ``glm-coding-plan-quota-endpoint``):
- ``data.limits`` entries are matched by ``type`` + ``unit``:
  ``TOKENS_LIMIT`` unit 3 -> 5h session, unit 6 -> weekly;
  ``TIME_LIMIT`` -> monthly web searches.
- ``percentage`` is USED percent (remaining = 100 - percentage).
- ``nextResetTime`` is epoch milliseconds, preserved verbatim.
- ``level`` -> plan_level.
- ``number`` is NOT stable across accounts; never match on it.
- Auth for open.bigmodel.cn is the RAW api key (no ``Bearer`` prefix).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)

#: Quota endpoint path on open.bigmodel.cn / api.z.ai (both stations share it).
QUOTA_PATH = "/api/monitor/usage/quota/limit"

#: unit discriminator inside a ``TOKENS_LIMIT`` entry: 3 = 5h session.
_SESSION_UNIT = 3
#: unit discriminator inside a ``TOKENS_LIMIT`` entry: 6 = weekly.
_WEEKLY_UNIT = 6

#: business ``code`` values in a 2xx body that mean "auth failed" (slice-077
#: records the same codes for the sibling account/rateLimits contract).
_AUTH_BUSINESS_CODES = {401, 1001}


# --------------------------------------------------------------------- transport


@dataclass(frozen=True)
class FetchResponse:
    """Minimal HTTP response shape the client consumes.

    ``body`` is the parsed JSON object, or ``None`` when the body was absent
    or not a JSON object (the client maps that to ``server-error``).
    """

    status: int
    body: Mapping[str, Any] | None


class NetworkError(Exception):
    """A transport-level connection/timeout failure.

    The client maps this to ``QuotaStatus.NETWORK_ERROR``. Fetchers raise it
    so the client never has to know about httpx / urllib specifics.
    """


#: Injected transport: given (url, headers), return a response or raise
#: ``NetworkError``. Real binding: ``httpx_fetcher``; tests pass a fake.
AsyncFetcher = Callable[[str, Mapping[str, str]], Awaitable[FetchResponse]]


def httpx_fetcher(client: httpx.AsyncClient, *, timeout: float = 10.0) -> AsyncFetcher:
    """Build an ``AsyncFetcher`` backed by a shared ``httpx.AsyncClient``.

    ``httpx.HTTPError`` (connect / read / timeout) is translated to
    ``NetworkError``; a non-object JSON body becomes ``body=None`` (the client
    treats that as ``server-error``).
    """

    async def fetch(url: str, headers: Mapping[str, str]) -> FetchResponse:
        try:
            resp = await client.get(url, headers=dict(headers), timeout=timeout)
        except httpx.HTTPError as exc:
            raise NetworkError(str(exc)) from exc
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None
        body = parsed if isinstance(parsed, Mapping) else None
        return FetchResponse(resp.status_code, body)

    return fetch


# --------------------------------------------------------------------- parsing


def _as_float(value: Any) -> float | None:
    """Coerce to a finite float, tolerating numeric strings; else ``None``.

    Rejects ``bool`` (an ``int`` subclass) and NaN.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    """Coerce to ``int`` (e.g. epoch ms), or ``None``; rejects ``bool``."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _find_limit(
    limits: list[Mapping[str, Any]], type_: str, unit: int | None
) -> Mapping[str, Any] | None:
    """First limit of ``type_`` matching ``unit`` (or unit-less fallback).

    Ported from the KiwiGaze ``findLimit``: match by ``type``; when a ``unit``
    is requested, prefer an exact match, falling back to the first entry whose
    unit is unset. ``unit`` is the stable discriminator; ``number`` is not.
    """

    fallback: Mapping[str, Any] | None = None
    for item in limits:
        if item.get("type") != type_:
            continue
        item_unit = item.get("unit")
        if unit is None:
            return item
        if item_unit == unit:
            return item
        if fallback is None and item_unit is None:
            fallback = item
    return fallback


def _window(
    kind: QuotaWindowKind, limit: Mapping[str, Any] | None
) -> QuotaWindow | None:
    """Build a ``QuotaWindow`` from a raw limit, or ``None`` if unusable."""

    if limit is None:
        return None
    used = _as_float(limit.get("percentage"))
    if used is None and kind is QuotaWindowKind.WEB_SEARCHES_MONTHLY:
        # TIME_LIMIT may omit percentage; derive from currentValue / usage.
        used_val = _as_float(limit.get("currentValue"))
        cap = _as_float(limit.get("usage"))
        if used_val is not None and cap:
            used = used_val / cap * 100.0
    if used is None:
        return None
    return QuotaWindow(
        kind=kind,
        used_percent=used,
        resets_at=_as_int(limit.get("nextResetTime")),
        raw=dict(limit),
    )


def _extract_limits(
    raw: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
    """Pull the ``limits`` list + its container out of a GLM response.

    Tolerates both ``data.limits`` and a bare top-level array (the KiwiGaze
    client does too). Returns ``([], {})`` when no list is found.
    """

    data = raw.get("data")
    container: Mapping[str, Any] = (
        data if isinstance(data, Mapping) else raw if isinstance(raw, Mapping) else {}
    )
    maybe = container.get("limits") if isinstance(container, Mapping) else None
    if isinstance(maybe, list):
        # Drop non-object entries so a malformed response (e.g. ``limits: [7]``)
        # can never reach ``item.get`` and raise — parse must map junk to
        # no-data, never crash (slice-093-pre criterion 3).
        return [item for item in maybe if isinstance(item, Mapping)], container
    if isinstance(container, list):
        return list(container), {}
    return [], container


def parse_glm_quota(
    raw: Mapping[str, Any], *, account_id: str, fetched_at: int
) -> QuotaSnapshot:
    """Map a GLM quota response to a ``QuotaSnapshot``.

    Empty / missing ``limits`` -> ``NO_DATA`` (never raises): the endpoint is
    undocumented and its shape may change.
    """

    limits, container = _extract_limits(raw)
    if not limits:
        return _snapshot(account_id, fetched_at, QuotaStatus.NO_DATA)

    windows: list[QuotaWindow] = []
    for kind, type_, unit in (
        (QuotaWindowKind.SESSION_5H, "TOKENS_LIMIT", _SESSION_UNIT),
        (QuotaWindowKind.WEEKLY, "TOKENS_LIMIT", _WEEKLY_UNIT),
        (QuotaWindowKind.WEB_SEARCHES_MONTHLY, "TIME_LIMIT", None),
    ):
        window = _window(kind, _find_limit(limits, type_, unit))
        if window is not None:
            windows.append(window)

    level = container.get("level") if isinstance(container, Mapping) else None
    plan_level = level if isinstance(level, str) and level else None

    status = QuotaStatus.OK if windows else QuotaStatus.NO_DATA
    return QuotaSnapshot(
        provider=Provider.GLM,
        account_id=account_id,
        plan_level=plan_level,
        windows=tuple(windows),
        fetched_at=fetched_at,
        status=status,
    )


# --------------------------------------------------------------------- client


def _snapshot(
    account_id: str, fetched_at: int, status: QuotaStatus
) -> QuotaSnapshot:
    """A window-less GLM snapshot in the given status."""

    return QuotaSnapshot(
        provider=Provider.GLM,
        account_id=account_id,
        plan_level=None,
        windows=(),
        fetched_at=fetched_at,
        status=status,
    )


def _map_response(
    resp: FetchResponse, *, account_id: str, fetched_at: int
) -> QuotaSnapshot:
    """Map an HTTP response to a snapshot, never raising."""

    if resp.status in (401, 403):
        return _snapshot(account_id, fetched_at, QuotaStatus.AUTH_ERROR)
    if resp.status < 200 or resp.status >= 500:
        return _snapshot(account_id, fetched_at, QuotaStatus.SERVER_ERROR)
    body = resp.body
    if not isinstance(body, Mapping):
        return _snapshot(account_id, fetched_at, QuotaStatus.SERVER_ERROR)
    code = body.get("code")
    if code in _AUTH_BUSINESS_CODES:
        return _snapshot(account_id, fetched_at, QuotaStatus.AUTH_ERROR)
    # A business failure that is not auth (e.g. code 500, success:false) is a
    # server-side problem, not "no data yet".
    if isinstance(code, int) and code != 200:
        return _snapshot(account_id, fetched_at, QuotaStatus.SERVER_ERROR)
    return parse_glm_quota(body, account_id=account_id, fetched_at=fetched_at)


def _default_now_ms() -> int:
    """Epoch milliseconds for snapshot stamping."""

    return int(time.time() * 1000)


class GlmQuotaClient:
    """Fetches one GLM Coding Plan account's quota.

    Auth is the RAW api key (no ``Bearer``) per the open.bigmodel.cn contract.
    Every failure maps to a non-ok ``QuotaStatus``; ``fetch`` never raises.

    Args:
        host: scheme + host (no path). Default is the China station.
        fetcher: injected transport (real: ``httpx_fetcher``; tests: a fake).
        now_ms: epoch-ms clock for ``fetched_at`` (injectable for fake clocks).
        timeout: per-request timeout in seconds (forwarded by the real fetcher).
    """

    def __init__(
        self,
        host: str = "https://open.bigmodel.cn",
        *,
        fetcher: AsyncFetcher,
        now_ms: Callable[[], int] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._host = host.rstrip("/")
        self._fetcher = fetcher
        self._now_ms = now_ms or _default_now_ms
        self._timeout = timeout

    async def fetch(self, account_id: str, api_key: str) -> QuotaSnapshot:
        """Fetch and map the quota snapshot for one account."""

        url = f"{self._host}{QUOTA_PATH}"
        headers: Mapping[str, str] = {
            "Authorization": api_key,
            "Accept": "application/json",
        }
        fetched_at = int(self._now_ms())
        try:
            resp = await self._fetcher(url, headers)
        except NetworkError:
            return _snapshot(account_id, fetched_at, QuotaStatus.NETWORK_ERROR)
        return _map_response(resp, account_id=account_id, fetched_at=fetched_at)
