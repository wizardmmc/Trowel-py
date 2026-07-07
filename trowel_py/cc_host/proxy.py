"""Local reverse proxy for CC's /v1/messages — slice-030.

Why this exists: CC ``-p`` mode sends a ``"You are a Claude agent..."`` system
prompt (TUI sends ``"You are Claude Code..."``). 智谱 GLM caches by the
``system`` + ``tools`` prefix; the ``-p`` prefix is cold, so during overload
windows every ``-p`` request hits ``529 [1305]`` while the TUI stays up. This
proxy rewrites the ``-p`` system identity block to the TUI version so the
request lands on the same hot cache.

The proxy is mounted on the existing trowel FastAPI app (same process/port),
intercepts ``POST /v1/messages`` (plus ``POST /v1/{rest}`` passthrough for
``count_tokens`` etc.), and streams the SSE response back without buffering.

Provider routing: CC strips settings-sourced provider vars when
``CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST`` is set, so the launcher must re-inject
them into the spawn env (``ANTHROPIC_BASE_URL`` swapped for this proxy).
``build_proxy_env`` produces that delta.
"""
from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

# TUI identity sentence — the hot-cache prefix 智谱 sees from every CC TUI user.
# Hardcoded default; a config override (added with the router wiring) lets users
# patch this without a code change if a future CC release alters it.
TUI_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# Identity-block fingerprints. A system text block whose text starts with any
# of these is the identity we rewrite (or recognize as already-TUI). Matched by
# content, not by array index — CC's system array shape varies across requests.
_IDENTITY_PREFIXES: tuple[str, ...] = (
    "You are Claude Code",
    "You are a Claude agent",
)

# CC -p prepends a billing-header system block whose text starts with this
# (e.g. "x-anthropic-billing-header: cc_version=2.1.197.123; cc_entrypoint=...").
# TUI's system array has no such block. 智谱 caches by the whole system array,
# so as long as this block is present the cache key can't match TUI's hot
# cache -> cold -> 529 during overload. Empirically pinned by the slice-030
# variant replay (V5 vs V6): keeping this block — even moved off index 0 —
# -> 529; dropping it -> 200; tools unchanged. ``replace_system_identity``
# drops every block whose text matches this prefix.
_BILLING_HEADER_PREFIX = "x-anthropic-billing-header"

# Upstream hosts known to cache by system prefix in a way that hurts -p. Only
# these get the rewrite; official Anthropic and other providers pass through.
_REPLACE_HOSTS: tuple[str, ...] = ("bigmodel.cn",)

# Debug dump (env-gated, slice-030 diagnostics): when PROXY_DEBUG is set, each
# forward writes a JSON summary to /tmp/cc-proxy-dump/ capturing the system
# blocks + tool names (before/after the rewrite), the upstream status, and the
# first 3 KB of the response body. Used to confirm whether the identity
# rewrite actually fires and what 智谱 returns during a 529 window. Off by
# default; zero overhead when unset. Temporary diagnostic — remove once the
# retry root cause is pinned.
_DUMP_DIR = Path("/tmp/cc-proxy-dump")
_DUMP_RESP_HEAD_BYTES = 3000


def _proxy_debug() -> bool:
    """True iff PROXY_DEBUG is set in the environment."""
    return bool(os.environ.get("PROXY_DEBUG"))


def _summarize_body(raw: bytes) -> dict:
    """Extract the cache-relevant shape of a /v1/messages body without dumping
    the full payload (tool definitions are huge — a real -p body is ~180 KB).

    Captures each system block's type / text head / length / cache_control
    presence, the tool names (not their bodies), message count, model, stream,
    and total body bytes. Enough to see whether the identity block was
    rewritten and how the -p tool set differs from TUI's.
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
    """Read the ``env`` block from a CC settings.json file.

    Args:
        settings_path: path to ``~/.claude/settings.json`` (or any settings
            file). May not exist.

    Returns:
        The env dict as ``{str: str}``, or ``{}`` if the file is missing, has
        no ``env`` block, or is malformed. Never raises — settings are
        best-effort user input and a bad file must not break the proxy.
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
    """Build the spawn-env delta that routes CC through this proxy.

    Merging ``os.environ`` is the caller's job — this returns only the proxy
    delta so it stays pure and testable. The delta:

      - every settings env var passed through (auth token / model / per-tier
        defaults) so CC still has them after ``PROVIDER_MANAGED`` strips the
        settings-sourced copies;
      - ``ANTHROPIC_BASE_URL`` overridden to the proxy URL;
      - ``CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=1`` so CC doesn't let
        ``~/.claude/settings.json`` override our routing.

    Args:
        settings_env: the env block loaded from settings.json.
        proxy_base_url: the proxy's base URL (e.g. ``http://127.0.0.1:8000``).

    Returns:
        A new dict of env vars to merge into the CC subprocess env.
    """
    delta = dict(settings_env)
    delta["ANTHROPIC_BASE_URL"] = proxy_base_url
    delta["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] = "1"
    return delta


def _is_billing_header_block(block: object) -> bool:
    """True iff a system block is CC -p's billing-header block.

    Matched by text prefix (``x-anthropic-billing-header``), ignoring leading
    whitespace, so ``cache_control`` or extra keys on the block don't hide it.
    TUI's system array has no such block; this is the -p-specific prefix that
    spoils 智谱's TUI cache match.
    """
    if not isinstance(block, dict):
        return False
    text = block.get("text")
    if not isinstance(text, str):
        return False
    return text.lstrip().startswith(_BILLING_HEADER_PREFIX)


def replace_system_identity(body: dict) -> dict:
    """Return a new body with the system identity rewritten to TUI's
    hot-cache shape: drop the -p billing-header block, then rewrite the
    identity sentence.

    Two things must hold for 智谱 to hit TUI's hot cache:

      1. The -p billing-header system block (``x-anthropic-billing-header:
         ...``) must be dropped — TUI has no such block, and 智谱 caches by
         the whole system array, so keeping it anywhere spoils the match.
      2. The identity sentence must be TUI's (``"You are Claude Code..."``).

    After the drop, the identity block naturally becomes ``system[0]``,
    matching TUI's prefix order. ``cache_control``, tools, messages, and every
    other system block are preserved verbatim. Pinned by the slice-030 variant
    replay (V5 vs V6): keeping the billing block -> 529, dropping it -> 200,
    tools unchanged.

    Graceful degradation: if ``system`` is not an array or no identity block is
    found, an equal copy is returned unchanged (the proxy must never block a
    request just because it couldn't find the block).

    Args:
        body: the parsed ``POST /v1/messages`` request body.

    Returns:
        A new dict; the input is never mutated (immutability).
    """
    system = body.get("system")
    if not isinstance(system, list):
        return copy.deepcopy(body)
    new_body = copy.deepcopy(body)
    new_body["system"] = [
        block for block in new_body["system"]
        if not _is_billing_header_block(block)
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
    """Decide whether to rewrite the system identity for this upstream.

    Only 智谱 GLM's anthropic-compatible endpoint is known to cache by system
    prefix in a way that hurts ``-p``; official Anthropic and other providers
    pass through unchanged.

    Args:
        real_base_url: the real upstream base URL (from settings.json).

    Returns:
        True iff the host matches a known cache-discriminating provider.
    """
    return any(host in real_base_url for host in _REPLACE_HOSTS)


# Hop-by-hop headers (RFC 7230) plus host/content-length, which httpx
# recomputes from the real url/body. Strip from both request and response
# forwarding; everything else (auth, anthropic-*, content-type) passes through.
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
    """Drop hop-by-hop headers (and host/content-length, which httpx
    recomputes from the real url/body). Preserve everything else verbatim,
    including auth (x-api-key, authorization) and anthropic-* headers.

    Args:
        headers: a starlette/httpx Headers object or a dict — anything with
            ``.items()``.

    Returns:
        A plain ``{str: str}`` dict safe to forward.
    """
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _maybe_rewrite_system(raw: bytes, real_base_url: str) -> bytes:
    """Return the body bytes to forward: rewritten (TUI system identity) for
    cache-discriminating upstreams, raw passthrough otherwise. On any parse
    failure or missing identity block, return the original bytes unchanged
    (graceful degrade — the proxy must never block a request, spec Q6).

    Args:
        raw: the raw request body bytes.
        real_base_url: the real upstream base URL.

    Returns:
        Bytes to forward (possibly rewritten).
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
    # Compact + non-ASCII-preserved: only the identity block matters for cache
    # hit, so keep the rest as close to the original byte shape as we can.
    return json.dumps(new_body, ensure_ascii=False, separators=(",", ":")).encode()


async def _forward(request: Request, path: str) -> StreamingResponse:
    """Read the incoming request, optionally rewrite the system identity, then
    stream-forward to the real endpoint and pipe the response back without
    buffering. One code path for /v1/messages and the /v1/{rest} passthrough.

    When ``PROXY_DEBUG`` is set, also write a JSON dump per request to
    ``/tmp/cc-proxy-dump/`` (see ``_summarize_body``) capturing the system
    blocks + tool names before/after the rewrite and the first 3 KB of the
    upstream response — enough to see whether the rewrite fired and what 智谱
    returned during a 529 window.

    Args:
        request: the incoming FastAPI request (CC → proxy).
        path: the path under /v1/ to forward (e.g. ``v1/messages``).

    Returns:
        A StreamingResponse that pipes upstream chunks straight through.
    """
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

    # Collect up to the first 3 KB of the response body (best-effort, in the
    # pipe loop) so a 529's error JSON is captured without buffering a full
    # streaming 200 SSE response.
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
                    pass  # dump is best-effort; never block the response path

    return StreamingResponse(
        pipe(),
        status_code=upstream_resp.status_code,
        headers=_filter_headers(upstream_resp.headers),
    )


router = APIRouter()


@router.post("/v1/messages")
async def proxy_messages(request: Request) -> StreamingResponse:
    """Rewrite the -p system identity to the TUI version (for 智谱) and
    stream-forward to the real endpoint. CC sees this endpoint as its
    ANTHROPIC_BASE_URL; the rewrite is transparent to CC.
    """
    return await _forward(request, "v1/messages")


@router.post("/v1/{rest:path}")
async def proxy_passthrough(request: Request, rest: str) -> StreamingResponse:
    """Passthrough for every other /v1/* path CC calls (count_tokens, etc.).
    Shares the rewrite path with /v1/messages; the identity rewrite is a
    no-op on count_tokens bodies but keeps a single code path.
    """
    return await _forward(request, f"v1/{rest}")
