"""通过 Agent Host 内部 HTTP 接口访问跨 MCP 进程的交互委派。"""

from __future__ import annotations

from typing import Any

import httpx

from trowel_py.agent_mcp.http_errors import agent_api_error_detail
from trowel_py.agent_mcp.interactive_errors import InteractiveDelegationError

_AGENT_HOST_REQUEST_TIMEOUT_SECONDS = 45.0


class InteractiveBrokerClient:
    """把 stdio MCP 工具调用转换为无状态的 Agent Host HTTP 请求。"""

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """保存 Agent Host 地址、认证请求头和测试可替换的 HTTP transport。

        Args:
            base_url: Trowel Agent API 的根地址。
            transport: 测试中替换真实网络请求的可选 transport。
            headers: 桌面模式下访问 Agent Host API 所需的请求头。
        """

        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._headers = dict(headers or {})

    def _client(self) -> httpx.AsyncClient:
        """创建在 MCP deadline 前明确失败的 Agent Host 客户端。"""

        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                _AGENT_HOST_REQUEST_TIMEOUT_SECONDS,
                connect=5.0,
            ),
            transport=self._transport,
            headers=self._headers,
        )

    async def start(
        self,
        *,
        parent_session_id: str,
        task: str,
        create_body: dict[str, Any],
    ) -> dict[str, Any]:
        """让 Agent Host 登记后台委派并返回初始快照。"""

        return await self._request(
            "POST",
            "/api/agent/internal/delegations",
            json={
                "parent_session_id": parent_session_id,
                "task": task,
                "create_body": create_body,
            },
        )

    async def respond(
        self,
        delegation_id: str,
        answers: dict[str, str],
        *,
        parent_session_id: str,
    ) -> dict[str, Any]:
        """把答案交给 Agent Host 中仍在运行的委派。"""

        return await self._request(
            "POST",
            f"/api/agent/internal/delegations/{delegation_id}/answers",
            json={
                "parent_session_id": parent_session_id,
                "answers": answers,
            },
        )

    async def status(
        self,
        delegation_id: str,
        *,
        parent_session_id: str,
    ) -> dict[str, Any]:
        """立即读取 Agent Host 保存的委派快照。"""

        return await self._request(
            "GET",
            f"/api/agent/internal/delegations/{delegation_id}",
            params={"parent_session_id": parent_session_id},
        )

    async def close(
        self,
        delegation_id: str,
        *,
        parent_session_id: str,
    ) -> dict[str, Any]:
        """要求 Agent Host 收敛 child 并删除委派句柄。"""

        return await self._request(
            "DELETE",
            f"/api/agent/internal/delegations/{delegation_id}",
            params={"parent_session_id": parent_session_id},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """执行一次内部请求并提取统一响应中的 data 快照。

        Args:
            method: HTTP 方法。
            path: Agent Host 内部接口路径。
            json: 可选 JSON 请求体。
            params: 可选查询参数。

        Returns:
            Agent Host 返回的委派快照。

        Raises:
            InteractiveDelegationError: Agent Host 拒绝请求或响应缺少 data。
        """

        async with self._client() as client:
            response = await client.request(method, path, json=json, params=params)
        error = agent_api_error_detail(response)
        if error is not None:
            raise InteractiveDelegationError(error)
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise InteractiveDelegationError(
                "Agent Host interactive delegation response has no data"
            )
        return dict(data)
