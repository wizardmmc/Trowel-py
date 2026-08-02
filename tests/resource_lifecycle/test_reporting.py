"""验证 Trowel 自有子进程在服务前向 sidecar 回报 PID。"""

from __future__ import annotations

import pytest

from trowel_py.resource_lifecycle import reporting


class FakeResponse:
    """记录测试响应已通过状态检查。"""

    def raise_for_status(self) -> None:
        """模拟成功的 HTTP 状态。"""


class FakeClient:
    """保存登记请求，不执行真实网络访问。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict, dict]] = []

    async def __aenter__(self):
        """返回当前假客户端。"""

        return self

    async def __aexit__(self, *_args) -> None:
        """结束假客户端上下文。"""

    async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        """记录登记端点、认证头和 PID 请求体。"""

        self.requests.append((url, headers, json))
        return FakeResponse()


async def test_report_current_process_skips_browser_mode() -> None:
    """没有登记环境时不应尝试网络访问。"""

    assert await reporting.report_current_process({}) is False


async def test_report_current_process_requires_complete_environment() -> None:
    """半套环境不能让子进程在未登记状态继续提供工具。"""

    with pytest.raises(RuntimeError, match="incomplete"):
        await reporting.report_current_process(
            {"TROWEL_RESOURCE_REGISTRATION_TOKEN": "private-token"}
        )


async def test_report_current_process_posts_current_pid(monkeypatch) -> None:
    """完整环境应使用 Bearer 凭据和当前 PID 登记。"""

    client = FakeClient()
    monkeypatch.setattr(reporting.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(reporting.os, "getpid", lambda: 410)
    environment = {
        "TROWEL_RESOURCE_REGISTRATION_URL": "http://127.0.0.1/register",
        "TROWEL_RESOURCE_REGISTRATION_CREDENTIAL": "private-credential",
        "TROWEL_RESOURCE_REGISTRATION_TOKEN": "private-token",
    }

    assert await reporting.report_current_process(environment) is True
    assert client.requests == [
        (
            "http://127.0.0.1/register",
            {"Authorization": "Bearer private-credential"},
            {"token": "private-token", "pid": 410},
        )
    ]
