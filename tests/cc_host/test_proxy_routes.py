"""Tests for cc_host.proxy router + streaming — slice-030 round 2.

A mini FastAPI app mounts only the proxy router. The upstream httpx client
is a fake injected via ``app.state.cc_http_client`` so the tests never hit the
real network.
"""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.cc_host.proxy import TUI_SYSTEM_IDENTITY
from trowel_py.cc_host.proxy import router as proxy_router


class FakeRequest:
    """Stand-in for httpx.Request — records what the proxy forwarded."""

    def __init__(self, method, url, headers, content):
        self.method = method
        self.url = url
        self.headers = headers or {}
        self.content = content or b""


class FakeResponse:
    """Stand-in for httpx.Response — yields raw chunks from aiter_raw()."""

    def __init__(self, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/event-stream"}
        self._chunks = chunks if chunks is not None else [b"data: ok\n\n"]

    async def aiter_raw(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass


class FakeClient:
    """Stand-in for httpx.AsyncClient — captures the forwarded request."""

    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.sent: list[FakeRequest] = []

    def build_request(self, method, url, **kwargs):
        return FakeRequest(method, url, kwargs.get("headers"), kwargs.get("content"))

    async def send(self, req, stream=False):
        self.sent.append(req)
        return self.response


def _make_app(real_base_url: str, client: FakeClient) -> TestClient:
    app = FastAPI()
    app.state.cc_http_client = client
    app.state.cc_real_base_url = real_base_url
    app.include_router(proxy_router)
    return TestClient(app)


def _p_body() -> dict:
    return {
        "system": [
            {"type": "text", "text": "You are a Claude agent, built on Anthropic..."},
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }


class TestProxyRouterReplace:
    def test_messages_replaces_system_for_bigmodel(self):
        client = FakeClient()
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        resp = tc.post("/v1/messages", json=_p_body())
        assert resp.status_code == 200
        sent = json.loads(client.sent[0].content)
        assert sent["system"][0]["text"] == TUI_SYSTEM_IDENTITY
        assert "bigmodel.cn" in str(client.sent[0].url)

    def test_messages_passthrough_for_official_anthropic(self):
        """非智谱后端：身份块保持原样，不替换（spec Q5）。"""
        client = FakeClient()
        tc = _make_app("https://api.anthropic.com", client)
        tc.post("/v1/messages", json=_p_body())
        sent = json.loads(client.sent[0].content)
        assert sent["system"][0]["text"] == "You are a Claude agent, built on Anthropic..."

    def test_count_tokens_route_not_404(self):
        """CC 会调 /v1/messages/count_tokens —— passthrough 路由不能 404。"""
        client = FakeClient()
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        resp = tc.post("/v1/messages/count_tokens", json={"messages": []})
        assert resp.status_code == 200
        assert "/v1/messages/count_tokens" in str(client.sent[0].url)


class TestProxyRouterStreaming:
    def test_sse_chunks_streamed_through(self):
        """逐 chunk 透传：上游几个 chunk → 客户端收到它们的拼接（顺序+完整）。"""
        chunks = [
            b"event: message_start\n\n",
            b"event: content_block_delta\ndata: ",
            b'{"x":1}\n\n',
        ]
        client = FakeClient(
            FakeResponse(200, {"content-type": "text/event-stream"}, chunks)
        )
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        resp = tc.post("/v1/messages", json=_p_body())
        assert resp.content == b"".join(chunks)

    def test_upstream_529_passed_through(self):
        """上游 529 原样透传给 CC，CC withRetry 自己重试（spec Q6）。"""
        client = FakeClient(
            FakeResponse(
                529,
                {"content-type": "application/json"},
                [b'{"error":"overloaded"}'],
            )
        )
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        resp = tc.post("/v1/messages", json=_p_body())
        assert resp.status_code == 529
        assert b"overloaded" in resp.content

    def test_response_content_type_preserved(self):
        client = FakeClient(
            FakeResponse(200, {"content-type": "application/json; charset=utf-8"}, [b"{}"])
        )
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        resp = tc.post("/v1/messages", json=_p_body())
        assert resp.headers["content-type"] == "application/json; charset=utf-8"


class TestProxyRouterHeaders:
    def test_auth_headers_forwarded(self):
        client = FakeClient()
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        tc.post(
            "/v1/messages",
            json=_p_body(),
            headers={"x-api-key": "sk-test", "anthropic-version": "2023-06-01"},
        )
        sent_lower = {k.lower(): v for k, v in client.sent[0].headers.items()}
        assert sent_lower.get("x-api-key") == "sk-test"
        assert sent_lower.get("anthropic-version") == "2023-06-01"

    def test_hop_by_hop_headers_stripped(self):
        """host / content-length 由 httpx 按真实 url/body 重设，透传时剥掉避免冲突。"""
        client = FakeClient()
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        tc.post("/v1/messages", json=_p_body())
        sent_lower = {k.lower() for k in client.sent[0].headers}
        assert "content-length" not in sent_lower
        assert "host" not in sent_lower


class TestProxyRouterFallback:
    def test_malformed_body_forwarded_as_is(self):
        """body 解析失败 → 原 raw 转发不阻断（spec Q6 兜底）。"""
        client = FakeClient()
        tc = _make_app("https://open.bigmodel.cn/api/anthropic", client)
        resp = tc.post(
            "/v1/messages",
            content=b"not json{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert client.sent[0].content == b"not json{"
