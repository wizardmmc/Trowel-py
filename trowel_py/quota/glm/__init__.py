"""作为 GLM Coding Plan 额度客户端和解析函数的稳定导入入口。"""

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
    """保存 GLM 额度接口的 HTTP 状态码和 JSON 对象响应。

    Attributes:
        status: GLM 额度接口返回的 HTTP 状态码。
        body: 解析后的 JSON 对象；响应不是合法 JSON 对象时为 None。
    """

    status: int
    body: Mapping[str, Any] | None


class NetworkError(Exception):
    """表示发送 GLM 额度请求时捕获到的 ``httpx.HTTPError``。"""


AsyncFetcher = Callable[
    [str, Mapping[str, str]],
    Awaitable[FetchResponse],
]


def httpx_fetcher(
    client: httpx.AsyncClient,
    *,
    timeout: float = 10.0,
) -> AsyncFetcher:
    """创建复用指定 httpx 客户端的 GLM 额度请求函数。

    Args:
        client: 发送额度请求的共享异步 HTTP 客户端。
        timeout: 传给 ``httpx.AsyncClient.get`` 的超时秒数，不是整次请求的总时限。

    Returns:
        接收请求地址和请求头、返回 ``FetchResponse`` 的异步函数。
    """

    async def fetch(
        url: str,
        headers: Mapping[str, str],
    ) -> FetchResponse:
        """请求一个 GLM 额度地址并返回统一响应。

        ``httpx.AsyncClient.get`` 抛出的 ``httpx.HTTPError`` 会转换为
        ``NetworkError``；解析 JSON 时抛出的 ``ValueError`` 会使响应体记为 None，
        其他异常继续抛给调用方。

        Args:
            url: GLM 额度接口的完整 URL。
            headers: 发送给额度接口的请求头。

        Returns:
            HTTP 状态码和解析后的 JSON 对象；响应不是 JSON 对象时，响应体为 None。

        Raises:
            NetworkError: httpx 发送请求时抛出 ``httpx.HTTPError``。
        """

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
    """把 int、float 或非空数值字符串转换为 float。

    bool、float NaN、无法解析的字符串和其他类型返回 None。float 正负无穷以及
    字符串 ``"nan"``、``"inf"``、``"-inf"`` 会保留为相应的非有限浮点值。
    """

    return _run_as_float(value)


def _as_int(value: Any) -> int | None:
    """把 int 或没有小数部分的 float 转换为 int，拒绝 bool 和字符串。"""

    return _run_as_int(value)


def _find_limit(
    limits: list[Mapping[str, Any]],
    type_: str,
    unit: int | None,
) -> Mapping[str, Any] | None:
    """按 GLM 的 ``type`` 和 ``unit`` 字段选择额度窗口。

    指定 ``unit`` 时优先精确匹配，找不到时退回同类型且没有 ``unit`` 的窗口。

    Args:
        limits: GLM 返回的额度窗口列表。
        type_: 目标窗口的 ``type`` 字段值。
        unit: 目标窗口的 ``unit`` 字段值；为 None 时返回首个同类型窗口。

    Returns:
        匹配的原始额度窗口；没有匹配项时为 None。
    """

    return _run_find_limit(limits, type_, unit)


def _window(
    kind: QuotaWindowKind,
    limit: Mapping[str, Any] | None,
) -> QuotaWindow | None:
    """把 GLM 原始额度项转换为统一额度窗口。

    优先把 ``percentage`` 转换为已用百分比；月度搜索窗口没有可转换的
    ``percentage`` 时，才用 ``currentValue / usage * 100`` 计算，``usage`` 为 0
    或无法转换时不计算。没有可用百分比时返回 None；转换结果不再校验有限性，
    也不限制在 0 到 100。
    """

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
    """从 ``data`` 对象或响应顶层提取 GLM 额度项。

    ``data`` 是对象时只读取其中的 ``limits``，否则读取顶层 ``limits``；
    列表中不是对象的元素会被忽略。

    Args:
        raw: GLM 额度接口返回的 JSON 对象。

    Returns:
        额度项列表，以及与 ``limits`` 同层、供读取 ``level`` 的对象。
    """

    return _run_extract_limits(raw, mapping_type=Mapping)


def parse_glm_quota(
    raw: Mapping[str, Any],
    *,
    account_id: str,
    fetched_at: int,
) -> QuotaSnapshot:
    """把 GLM 额度响应解析为统一额度快照。

    解析五小时、每周和月度搜索窗口；没有可用窗口时返回 ``NO_DATA`` 快照。

    Args:
        raw: GLM 额度接口返回的 JSON 对象。
        account_id: 记录在快照中的本地账号 ID。
        fetched_at: 发起额度读取时的 Unix 毫秒时间戳。
    """

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
    """创建指定状态且不含额度窗口的 GLM 快照。"""

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
    """根据 HTTP 状态、响应体和整数业务码生成额度快照。

    HTTP 401、403 以及整数业务码 401、1001 映射为 ``AUTH_ERROR``。HTTP 状态
    低于 200 或达到 500、响应体不是 JSON 对象，或者其他非 200 的整数业务码
    映射为 ``SERVER_ERROR``。其余响应会交给额度解析器，包括响应体可解析的其他
    3xx 和 4xx 响应。

    Args:
        resp: GLM 额度接口的统一 HTTP 响应。
        account_id: 记录在快照中的本地账号 ID。
        fetched_at: 发起额度读取时的 Unix 毫秒时间戳。
    """

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
    """返回当前 Unix 时间戳，单位为毫秒。"""

    return int(time.time() * 1000)


class GlmQuotaClient:
    """读取单个 GLM Coding Plan 账号并返回统一额度快照。"""

    def __init__(
        self,
        host: str = "https://open.bigmodel.cn",
        *,
        fetcher: AsyncFetcher,
        now_ms: Callable[[], int] | None = None,
        timeout: float = 10.0,
    ) -> None:
        """配置额度接口根地址、请求函数和取时函数。

        Args:
            host: GLM 额度接口的 HTTPS 根地址。
            fetcher: 接收完整 URL 和请求头的异步请求函数。
            now_ms: 返回当前 Unix 毫秒时间戳的函数；为 None 时读取系统时间。
            timeout: 保留的请求超时秒数；实际请求超时由传入的 ``fetcher`` 决定。
        """

        self._host = host.rstrip("/")
        self._fetcher = fetcher
        self._now_ms = now_ms or _default_now_ms
        self._timeout = timeout

    async def fetch(
        self,
        account_id: str,
        api_key: str,
    ) -> QuotaSnapshot:
        """读取一个 GLM Coding Plan 账号的额度。

        本函数只把凭据放入传给请求函数的 ``Authorization`` 请求头，不把凭据
        写入返回快照。``NetworkError`` 转换为 ``NETWORK_ERROR`` 快照，其他异常
        继续抛给调用方。

        Args:
            account_id: 记录在快照中的本地账号 ID。
            api_key: 调用 GLM 额度接口所用的 API key。

        Returns:
            该账号本次读取的统一额度快照。
        """

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
