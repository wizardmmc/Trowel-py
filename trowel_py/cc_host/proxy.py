"""为 CC 提供流式本地反代，并为已验证的上游统一请求缓存前缀。"""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

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


def _proxy_debug() -> bool:
    return bool(os.environ.get("PROXY_DEBUG"))


def _summarize_body(raw: bytes) -> dict:
    """提取缓存诊断摘要，不记录完整 prompt、message 或 tool schema。"""
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
    """读取 CC settings 的 env；文件缺失或损坏时按空配置降级。"""
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
    """构造由调用方合并的 env 增量，固定反代路由并保留 provider 配置。"""
    delta = dict(settings_env)
    delta["ANTHROPIC_BASE_URL"] = proxy_base_url
    delta["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] = "1"
    return delta


def _is_billing_header_block(block: object) -> bool:
    if not isinstance(block, dict):
        return False
    text = block.get("text")
    if not isinstance(text, str):
        return False
    return text.lstrip().startswith(_BILLING_HEADER_PREFIX)


def replace_system_identity(body: dict) -> dict:
    """删除 -p billing block 并替换 TUI identity，始终返回独立副本。

    其他 system block、cache_control、tools 与 messages 保持原样；无法识别
    system 时返回等值副本，不能因缓存优化阻断请求。
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
    """仅对已有缓存差分证据的上游启用 rewrite。"""
    return any(host in real_base_url for host in _REPLACE_HOSTS)


# httpx 会按真实 URL/body 重建 host 与 content-length；其余逐跳 header 也不转发。
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
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _maybe_rewrite_system(raw: bytes, real_base_url: str) -> bytes:
    """只为目标上游重写 JSON；空 body 或解析失败时原字节透传。"""
    if not raw or not should_replace(real_base_url):
        return raw
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw
    if not isinstance(body, dict):
        return raw
    new_body = replace_system_identity(body)
    # rewrite 会重新序列化；紧凑编码避免额外扩大请求体。
    return json.dumps(new_body, ensure_ascii=False, separators=(",", ":")).encode()


async def _forward(request: Request, path: str) -> StreamingResponse:
    """按原顺序流式转发上游响应，并在消费结束或断开时关闭 response。"""
    client = request.app.state.cc_http_client
    real_base_url = request.app.state.cc_real_base_url

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
    upstream_resp = await client.send(upstream_req, stream=True)

    if dump_rec is not None:
        dump_rec["response_status"] = upstream_resp.status_code

    # 诊断只旁路收集前 3 KB，不能缓冲或延迟 SSE 主链路。
    dump_buf: bytearray | None = bytearray() if debug else None

    async def pipe() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_resp.aiter_raw():
                if dump_buf is not None and len(dump_buf) < _DUMP_RESP_HEAD_BYTES:
                    dump_buf.extend(chunk[: _DUMP_RESP_HEAD_BYTES - len(dump_buf)])
                yield chunk
        finally:
            await upstream_resp.aclose()
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


@router.post("/v1/messages")
async def proxy_messages(request: Request) -> StreamingResponse:
    """按上游门禁重写 `-p` 的 system identity，并把响应流式转发到真实端点。

    CC 通过 `ANTHROPIC_BASE_URL` 调用本路由，重写过程对 CC 透明。
    """
    return await _forward(request, "v1/messages")


@router.post("/v1/{rest:path}")
async def proxy_passthrough(request: Request, rest: str) -> StreamingResponse:
    """流式转发 CC 调用的其他 `/v1/*` 路径，例如 `count_tokens`。

    所有路径与 `/v1/messages` 共用请求重写和上游转发链路。
    """
    return await _forward(request, f"v1/{rest}")
