"""GLM Coding Plan 额度的稳定解析与客户端入口。"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from trowel_py.quota.glm.parser import as_float as _run_as_float
from trowel_py.quota.glm.parser import as_int as _run_as_int
from trowel_py.quota.glm.parser import extract_limits as _run_extract_limits
from trowel_py.quota.glm.parser import find_limit as _run_find_limit
from trowel_py.quota.glm.parser import parse_quota as _run_parse_quota
from trowel_py.quota.glm.parser import window as _run_window
from trowel_py.quota.glm.transport import fetch as _run_httpx_fetch
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)

QUOTA_PATH = "/api/monitor/usage/quota/limit"
_SESSION_UNIT = 3
_WEEKLY_UNIT = 6
_AUTH_BUSINESS_CODES = {401, 1001}


@dataclass(frozen=True)
class FetchResponse:
    status: int
    body: Mapping[str, Any] | None


class NetworkError(Exception):
    """由 transport 归一化的连接或超时错误。"""


AsyncFetcher = Callable[
    [str, Mapping[str, str]],
    Awaitable[FetchResponse],
]


def httpx_fetcher(
    client: httpx.AsyncClient,
    *,
    timeout: float = 10.0,
) -> AsyncFetcher:
    async def fetch(
        url: str,
        headers: Mapping[str, str],
    ) -> FetchResponse:
        return await _run_httpx_fetch(
            client,
            url,
            headers,
            timeout=timeout,
            http_error_type=httpx.HTTPError,
            network_error_type=NetworkError,
            response_type=FetchResponse,
            mapping_type=Mapping,
        )

    return fetch


def _as_float(value: Any) -> float | None:
    """保留既有宽松数值转换；非有限字符串也按 float 接受。"""
    return _run_as_float(value)


def _as_int(value: Any) -> int | None:
    return _run_as_int(value)


def _find_limit(
    limits: list[Mapping[str, Any]],
    type_: str,
    unit: int | None,
) -> Mapping[str, Any] | None:
    return _run_find_limit(limits, type_, unit)


def _window(
    kind: QuotaWindowKind,
    limit: Mapping[str, Any] | None,
) -> QuotaWindow | None:
    return _run_window(
        kind,
        limit,
        as_float=_as_float,
        as_int=_as_int,
        window_type=QuotaWindow,
        monthly_kind=QuotaWindowKind.WEB_SEARCHES_MONTHLY,
    )


def _extract_limits(
    raw: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
    return _run_extract_limits(raw, mapping_type=Mapping)


def parse_glm_quota(
    raw: Mapping[str, Any],
    *,
    account_id: str,
    fetched_at: int,
) -> QuotaSnapshot:
    """宽松解析未公开的 provider payload，缺少可用窗口时返回 NO_DATA。"""
    return _run_parse_quota(
        raw,
        account_id=account_id,
        fetched_at=fetched_at,
        extract_limits=_extract_limits,
        find_limit=_find_limit,
        build_window=_window,
        snapshot_without_windows=_snapshot,
        snapshot_type=QuotaSnapshot,
        provider=Provider.GLM,
        ok_status=QuotaStatus.OK,
        no_data_status=QuotaStatus.NO_DATA,
        session_kind=QuotaWindowKind.SESSION_5H,
        weekly_kind=QuotaWindowKind.WEEKLY,
        monthly_kind=QuotaWindowKind.WEB_SEARCHES_MONTHLY,
        session_unit=_SESSION_UNIT,
        weekly_unit=_WEEKLY_UNIT,
        mapping_type=Mapping,
    )


def _snapshot(
    account_id: str,
    fetched_at: int,
    status: QuotaStatus,
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=Provider.GLM,
        account_id=account_id,
        plan_level=None,
        windows=(),
        fetched_at=fetched_at,
        status=status,
    )


def _map_response(
    resp: FetchResponse,
    *,
    account_id: str,
    fetched_at: int,
) -> QuotaSnapshot:
    """把 HTTP 与业务状态映射为额度快照。"""
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
    if isinstance(code, int) and code != 200:
        return _snapshot(account_id, fetched_at, QuotaStatus.SERVER_ERROR)
    return parse_glm_quota(
        body,
        account_id=account_id,
        fetched_at=fetched_at,
    )


def _default_now_ms() -> int:
    return int(time.time() * 1000)


class GlmQuotaClient:
    """获取单个 GLM Coding Plan 账号的额度。"""

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

    async def fetch(
        self,
        account_id: str,
        api_key: str,
    ) -> QuotaSnapshot:
        """凭据只进入原始 Authorization header。"""
        url = f"{self._host}{QUOTA_PATH}"
        headers: Mapping[str, str] = {
            "Authorization": api_key,
            "Accept": "application/json",
        }
        fetched_at = int(self._now_ms())
        try:
            response = await self._fetcher(url, headers)
        except NetworkError:
            return _snapshot(
                account_id,
                fetched_at,
                QuotaStatus.NETWORK_ERROR,
            )
        return _map_response(
            response,
            account_id=account_id,
            fetched_at=fetched_at,
        )
