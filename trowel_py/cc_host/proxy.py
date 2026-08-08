"""为 CC 提供流式本地反代，并为已验证的上游统一请求缓存前缀。"""

from __future__ import annotations

import copy
import json
import os
import time
import secrets
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

# GLM 按 system/tools 前缀缓存；CC TUI 的 identity 前缀已有稳定热缓存。
TUI_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# system block 顺序会变化，identity 必须按内容而不是固定索引识别。
_IDENTITY_PREFIXES: tuple[str, ...] = (
    "You are Claude Code",
    "You are a Claude agent",
)

# 真实 529 差分表明，CC -p 独有的 billing block 会破坏 TUI 缓存命中。
_BILLING_HEADER_PREFIX = "x-anthropic-billing-header"

# 未经真实差分确认的 provider 必须原样透传。
_REPLACE_HOSTS: tuple[str, ...] = ("bigmodel.cn",)

# PROXY_DEBUG 默认关闭；诊断仍可能含请求摘要和响应正文，不得提交或外传。
_DUMP_DIR = Path("/tmp/cc-proxy-dump")
_DUMP_RESP_HEAD_BYTES = 3000


@dataclass(frozen=True)
class _ClaudeConnectionLease:
    """保存会话冻结的上游与可选出站代理，避免秘密出现在对象展示中。"""

    upstream_base_url: str
    proxy_url: str | None = field(default=None, repr=False)


class ClaudeConnectionProxyRegistry:
    """把不透明会话租约映射到冻结的 Claude 上游地址。

    registry 不持有 API key。Claude Code 自己发送的认证 header 仍由本地代理原样
    转发，因此路径租约泄露也不能单独访问第三方账号。
    """

    def __init__(self) -> None:
        """创建空 registry，并用互斥锁保护同步会话创建和异步请求读取。"""

        self._leases: dict[str, _ClaudeConnectionLease] = {}
        self._lock = threading.Lock()

    def acquire(self, upstream_base_url: str, proxy_url: str | None = None) -> str:
        """为冻结上游及其出站代理分配随机路径租约并返回令牌。"""

        token = secrets.token_urlsafe(32)
        with self._lock:
            self._leases[token] = _ClaudeConnectionLease(
                upstream_base_url=upstream_base_url.rstrip("/"),
                proxy_url=proxy_url,
            )
        return token

    def release(self, token: str) -> None:
        """幂等释放会话租约。"""

        with self._lock:
            self._leases.pop(token, None)

    def resolve(self, token: str) -> str | None:
        """读取租约对应的冻结上游；未知或已释放时返回 None。"""

        with self._lock:
            lease = self._leases.get(token)
            return lease.upstream_base_url if lease is not None else None

    def outbound_proxy(self, token: str) -> str | None:
        """读取租约的出站代理；未知租约与未配置代理都返回 None。"""

        with self._lock:
            lease = self._leases.get(token)
            return lease.proxy_url if lease is not None else None


def _proxy_debug() -> bool:
    """判断是否启用可能含敏感信息的本地反代诊断。

    `PROXY_DEBUG` 只要是非空字符串即视为启用。
    """

    return bool(os.environ.get("PROXY_DEBUG"))


def _summarize_body(raw: bytes) -> dict:
    """提取缓存诊断摘要，不展开 message 或 tool schema。

    system 文本和非法 JSON 只记录有限长度的前缀，但其中仍可能包含完整的短
    prompt 或其他敏感信息，只能用于本机诊断。空请求体、非法 JSON 和非对象
    JSON 分别返回带状态标记的摘要。
    """
    if not raw:
        return {"_empty": True}
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"_parse_error": True, "head": raw[:200].decode("utf-8", "replace")}
    if not isinstance(body, dict):
        return {"_not_dict": True}
    out: dict = {}
    sys_blocks = body.get("system")
    if isinstance(sys_blocks, list):
        blocks = []
        for b in sys_blocks:
            if not isinstance(b, dict):
                continue
            txt = b.get("text")
            blocks.append(
                {
                    "type": b.get("type"),
                    "text_head": txt[:160] if isinstance(txt, str) else None,
                    "text_len": len(txt) if isinstance(txt, str) else 0,
                    "has_cache_control": "cache_control" in b,
                    "keys": sorted(b.keys()),
                }
            )
        out["system_blocks"] = blocks
    elif isinstance(sys_blocks, str):
        out["system_str_head"] = sys_blocks[:200]
    tools = body.get("tools")
    if isinstance(tools, list):
        out["tools_count"] = len(tools)
        out["tools_names"] = [t.get("name") for t in tools if isinstance(t, dict)]
    out["messages_count"] = len(body.get("messages", []))
    out["model"] = body.get("model")
    out["stream"] = body.get("stream")
    out["max_tokens"] = body.get("max_tokens")
    out["body_bytes"] = len(raw)
    return out


def load_settings_env(settings_path: Path | str) -> dict[str, str]:
    """读取 CC settings 的 `env`，并把键和值统一转换为字符串。

    文件缺失、读取失败、JSON 损坏或 `env` 不是对象时返回空字典。文件若
    不是 UTF-8 编码，则由调用方处理 `UnicodeDecodeError`。
    """
    path = Path(settings_path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    env = data.get("env") if isinstance(data, dict) else None
    if not isinstance(env, dict):
        return {}
    return {str(k): str(v) for k, v in env.items()}


def build_proxy_env(
    settings_env: dict[str, str],
    proxy_base_url: str,
) -> dict[str, str]:
    """构造由调用方合并的环境变量增量。

    返回值复制 `settings_env`，覆盖 `ANTHROPIC_BASE_URL` 并标记 provider 由
    host 管理；本函数不读取或合并进程环境。
    """
    delta = dict(settings_env)
    delta["ANTHROPIC_BASE_URL"] = proxy_base_url
    delta["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] = "1"
    return delta


def _is_billing_header_block(block: object) -> bool:
    """判断 system 条目是否是 CC `-p` 附加的 billing block。"""

    if not isinstance(block, dict):
        return False
    text = block.get("text")
    if not isinstance(text, str):
        return False
    return text.lstrip().startswith(_BILLING_HEADER_PREFIX)


def replace_system_identity(body: dict) -> dict:
    """删除所有 `-p` billing block，并将首个可识别的 identity 替换为 TUI identity。

    其他 system block、cache_control、tools 与 messages 保持原值，返回结果始终
    与输入对象相互独立。`system` 不是列表时返回等值的深拷贝；列表中没有可识别
    的 identity 时仍会删除匹配的 billing block。
    """
    system = body.get("system")
    if not isinstance(system, list):
        return copy.deepcopy(body)
    new_body = copy.deepcopy(body)
    new_body["system"] = [
        block for block in new_body["system"] if not _is_billing_header_block(block)
    ]
    for block in new_body["system"]:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.startswith(_IDENTITY_PREFIXES):
            block["text"] = TUI_SYSTEM_IDENTITY
            break
    return new_body


def should_replace(real_base_url: str) -> bool:
    """根据 URL 是否包含已验证的 host 片段判断是否启用重写。"""
    return any(host in real_base_url for host in _REPLACE_HOSTS)


# 请求侧由 httpx 重建 host 和 content-length；请求与响应都不转发逐跳 header。
_HOP_BY_HOP: set[str] = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _filter_headers(headers) -> dict[str, str]:
    """移除请求或响应中不能端到端透传的 header。

    Args:
        headers: 待过滤的请求或响应 header 集合。
    """

    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _maybe_rewrite_system(raw: bytes, real_base_url: str) -> bytes:
    """只为目标上游重写 JSON 对象，并返回紧凑编码的 UTF-8 字节。

    空请求体、非目标上游、非法 JSON 或非对象 JSON 均原字节透传。
    """
    if not raw or not should_replace(real_base_url):
        return raw
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw
    if not isinstance(body, dict):
        return raw
    new_body = replace_system_identity(body)
    # 重写后会重新序列化；紧凑编码避免额外扩大请求体。
    return json.dumps(new_body, ensure_ascii=False, separators=(",", ":")).encode()


async def _forward(
    request: Request,
    path: str,
    *,
    real_base_url: str | None = None,
    outbound_proxy: str | None = None,
) -> StreamingResponse:
    """以 POST 流式转发请求，并在响应消费结束或断开时关闭上游连接。

    请求和响应都会过滤不能透传的 header。开启诊断时旁路收集响应前 3000 字节；
    最终诊断文件写入失败不会中断响应收尾。

    Args:
        request: 当前 FastAPI 请求；应用状态需提供共享 HTTP 客户端和真实上游地址。
        path: 拼接到真实上游地址后的相对路径。
    """
    owned_client = None
    if outbound_proxy:
        factory = getattr(
            request.app.state,
            "cc_proxy_client_factory",
            httpx.AsyncClient,
        )
        owned_client = factory(
            proxy=outbound_proxy,
            timeout=httpx.Timeout(None),
            trust_env=False,
        )
    client = owned_client or request.app.state.cc_http_client
    real_base_url = real_base_url or request.app.state.cc_real_base_url

    raw = await request.body()
    content = _maybe_rewrite_system(raw, real_base_url)

    debug = _proxy_debug()
    dump_rec: dict | None = None
    if debug:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        dump_rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "path": path,
            "real_base_url": real_base_url,
            "should_replace": should_replace(real_base_url),
            "rewrote": content != raw,
            "request_before": _summarize_body(raw),
            "request_after": _summarize_body(content),
        }

    headers = _filter_headers(request.headers)
    url = f"{real_base_url.rstrip('/')}/{path}"

    upstream_req = client.build_request("POST", url, headers=headers, content=content)
    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except BaseException:
        if owned_client is not None:
            await owned_client.aclose()
        raise

    if dump_rec is not None:
        dump_rec["response_status"] = upstream_resp.status_code

    # 诊断只旁路收集前 3 KB，不能缓冲或延迟 SSE 主链路。
    dump_buf: bytearray | None = bytearray() if debug else None

    async def pipe() -> AsyncIterator[bytes]:
        """逐块转发响应，并在流结束时关闭上游连接。

        Yields:
            上游响应的原始字节块。
        """

        try:
            async for chunk in upstream_resp.aiter_raw():
                if dump_buf is not None and len(dump_buf) < _DUMP_RESP_HEAD_BYTES:
                    dump_buf.extend(chunk[: _DUMP_RESP_HEAD_BYTES - len(dump_buf)])
                yield chunk
        finally:
            await upstream_resp.aclose()
            if owned_client is not None:
                await owned_client.aclose()
            if dump_rec is not None and dump_buf is not None:
                dump_rec["response_body_head"] = bytes(dump_buf).decode(
                    "utf-8", "replace"
                )
                dump_rec["response_body_collected_bytes"] = len(dump_buf)
                fname = (
                    time.strftime("%H%M%S-")
                    + f"{int(time.time() * 1000) % 1000:03d}"
                    + f"-{upstream_resp.status_code}.json"
                )
                try:
                    (_DUMP_DIR / fname).write_text(
                        json.dumps(dump_rec, ensure_ascii=False, indent=2)
                    )
                except OSError:
                    pass  # 诊断失败不能阻断响应

    return StreamingResponse(
        pipe(),
        status_code=upstream_resp.status_code,
        headers=_filter_headers(upstream_resp.headers),
    )


router = APIRouter()


@router.post("/api/cc-runtime/{lease_token}/v1/messages")
async def proxy_connection_messages(
    request: Request,
    lease_token: str,
) -> StreamingResponse:
    """按会话租约把 Claude Messages 请求流式转发到冻结上游。"""

    registry: ClaudeConnectionProxyRegistry = (
        request.app.state.cc_connection_proxy_registry
    )
    upstream = registry.resolve(lease_token)
    if upstream is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Claude connection lease not found")
    return await _forward(
        request,
        "v1/messages",
        real_base_url=upstream,
        outbound_proxy=registry.outbound_proxy(lease_token),
    )


@router.post("/api/cc-runtime/{lease_token}/v1/{rest:path}")
async def proxy_connection_passthrough(
    request: Request,
    lease_token: str,
    rest: str,
) -> StreamingResponse:
    """按会话租约转发 Claude 的其他 ``/v1`` 请求。"""

    registry: ClaudeConnectionProxyRegistry = (
        request.app.state.cc_connection_proxy_registry
    )
    upstream = registry.resolve(lease_token)
    if upstream is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Claude connection lease not found")
    return await _forward(
        request,
        f"v1/{rest}",
        real_base_url=upstream,
        outbound_proxy=registry.outbound_proxy(lease_token),
    )


@router.post("/v1/messages")
async def proxy_messages(request: Request) -> StreamingResponse:
    """按上游门禁重写 `-p` 的 system identity，并把响应流式转发到真实端点。

    CC 通过 `ANTHROPIC_BASE_URL` 调用本路由，重写过程对 CC 透明。
    """
    return await _forward(request, "v1/messages")


@router.post("/v1/{rest:path}")
async def proxy_passthrough(request: Request, rest: str) -> StreamingResponse:
    """流式转发 CC 调用的其他 `/v1/*` 路径，例如 `/v1/messages/count_tokens`。

    这些路径与 `/v1/messages` 共用请求重写和上游转发链路。
    """
    return await _forward(request, f"v1/{rest}")
