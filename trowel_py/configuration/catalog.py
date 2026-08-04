"""从自定义连接获取模型列表，并把第三方失败归一为稳定错误码。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from trowel_py.configuration.errors import ConfigurationError

_KNOWN_COMPAT_SUFFIXES = (
    "/api/claudecode",
    "/api/anthropic",
    "/apps/anthropic",
    "/api/coding",
    "/claudecode",
    "/anthropic",
    "/step_plan",
    "/coding",
    "/claude",
)
_MAX_MODEL_COUNT = 10_000
_MAX_MODEL_ID_CHARS = 512


@dataclass(frozen=True)
class FetchedModel:
    """表示上游模型列表中的一项公开模型。

    Attributes:
        id: 上游公开的 model ID。
        owned_by: 上游可选的所有者标签；不参与 capability 判断。
    """

    id: str
    owned_by: str | None = None


@dataclass(frozen=True)
class FetchedCatalog:
    """表示一次成功获取且完成去重的模型列表。

    Attributes:
        models: 按 model ID 排序的公开模型。
        source_endpoint: 实际返回成功响应的脱敏端点。
    """

    models: tuple[FetchedModel, ...]
    source_endpoint: str


def sanitize_url(value: str) -> str:
    """移除 URL 的 userinfo、query 和 fragment，只保留可公开地址。"""

    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if not host:
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def build_models_url_candidates(
    base_url: str,
    models_url: str | None = None,
) -> tuple[str, ...]:
    """按常见版本路径和 Anthropic 兼容后缀推导有限的模型列表端点。

    明确的模型列表地址优先。否则，版本段结尾使用 ``/models``；Anthropic
    兼容子路径还会尝试剥离后缀后的 ``/v1/models`` 和 ``/models``。结果保持
    首次出现顺序并去重。
    """

    if models_url and models_url.strip():
        return (models_url.strip().rstrip("/"),)
    root = base_url.strip().rstrip("/")
    if not root:
        raise ConfigurationError("INVALID_BASE_URL", "模型服务地址不能为空")
    last_segment = root.rsplit("/", 1)[-1]
    is_version = (
        last_segment.startswith("v")
        and len(last_segment) > 1
        and last_segment[1:].isdigit()
    )
    candidates = [f"{root}/models" if is_version else f"{root}/v1/models"]
    if is_version and last_segment != "v1":
        candidates.append(f"{root}/v1/models")
    for suffix in _KNOWN_COMPAT_SUFFIXES:
        if root.endswith(suffix):
            stripped = root[: -len(suffix)].rstrip("/")
            candidates.extend((f"{stripped}/v1/models", f"{stripped}/models"))
            break
    return tuple(dict.fromkeys(candidates))


class HttpModelCatalogFetcher:
    """通过受限 GET 请求获取 OpenAI 风格模型列表。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存总超时和可由测试替换的 HTTP transport。

        Args:
            timeout_seconds: 每个上游模型列表请求允许的最长秒数。
            transport: 实际发送 HTTP 的传输层；省略时使用 httpx 默认网络实现。
        """

        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch(
        self,
        *,
        base_url: str,
        api_key: str,
        models_url: str | None,
    ) -> FetchedCatalog:
        """获取、校验并去重模型列表，不把响应正文写入异常。"""

        if not api_key:
            raise ConfigurationError("SECRET_MISSING", "连接尚未配置 API key")
        candidates = build_models_url_candidates(base_url, models_url)
        endpoint_missing = False
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                for candidate in candidates:
                    response = await client.get(
                        candidate,
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if response.status_code in {404, 405}:
                        endpoint_missing = True
                        continue
                    if response.status_code in {401, 403}:
                        raise ConfigurationError(
                            "AUTH_FAILED", "模型服务拒绝了当前凭据", status_code=401
                        )
                    if not response.is_success:
                        raise ConfigurationError(
                            "NETWORK_ERROR",
                            "模型服务返回了无法识别的失败状态",
                            status_code=502,
                        )
                    return _decode_catalog(response, candidate)
        except httpx.TimeoutException as exc:
            raise ConfigurationError(
                "TIMEOUT", "获取模型列表超时", status_code=504
            ) from exc
        except httpx.RequestError as exc:
            raise ConfigurationError(
                "NETWORK_ERROR", "无法连接模型服务", status_code=502
            ) from exc
        if endpoint_missing:
            raise ConfigurationError(
                "ENDPOINT_NOT_FOUND",
                "模型服务没有可用的模型列表端点",
                status_code=404,
            )
        raise ConfigurationError(
            "NETWORK_ERROR", "无法获取模型列表", status_code=502
        )


def _decode_catalog(response: httpx.Response, source_endpoint: str) -> FetchedCatalog:
    """解析 OpenAI 风格响应，并拒绝空列表和不稳定 shape。"""

    try:
        payload = response.json()
    except ValueError as exc:
        raise ConfigurationError(
            "UNSUPPORTED_RESPONSE",
            "模型列表响应不是受支持的 JSON",
            status_code=502,
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ConfigurationError(
            "UNSUPPORTED_RESPONSE",
            "模型列表响应缺少 data 数组",
            status_code=502,
        )
    if len(data) > _MAX_MODEL_COUNT:
        raise ConfigurationError(
            "UNSUPPORTED_RESPONSE",
            "模型列表条目过多",
            status_code=502,
        )
    by_id: dict[str, FetchedModel] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if (
            not isinstance(model_id, str)
            or not model_id.strip()
            or len(model_id.strip()) > _MAX_MODEL_ID_CHARS
        ):
            continue
        owned_by = item.get("owned_by")
        by_id[model_id.strip()] = FetchedModel(
            id=model_id.strip(),
            owned_by=owned_by if isinstance(owned_by, str) else None,
        )
    if not by_id:
        raise ConfigurationError(
            "EMPTY_CATALOG", "模型服务返回了空列表", status_code=422
        )
    return FetchedCatalog(
        models=tuple(by_id[model_id] for model_id in sorted(by_id)),
        source_endpoint=sanitize_url(source_endpoint),
    )
