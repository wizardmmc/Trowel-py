"""Codex app-server 客户端请求的连接内状态。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

JsonObject = dict[str, Any]


@dataclass
class _TransportState:
    """管理 pending response 与连接关闭转换。"""

    pending: dict[str, asyncio.Future[JsonObject]] = field(default_factory=dict)
    closing: bool = False
    failed: bool = False

    @property
    def closed(self) -> bool:
        return self.closing or self.failed

    def begin_closing(self) -> bool:
        if self.closing:
            return False
        self.closing = True
        return True

    def register(self, request_id: str, future: asyncio.Future[JsonObject]) -> None:
        self.pending[request_id] = future

    def pop(self, request_id: str) -> asyncio.Future[JsonObject] | None:
        return self.pending.pop(request_id, None)

    def discard(self, request_id: str) -> None:
        self.pending.pop(request_id, None)

    def fail_all(self, error: BaseException) -> None:
        pending = self.pending
        self.pending = {}
        self.failed = True
        for future in pending.values():
            if not future.done():
                future.set_exception(error)


# 保留旧私有导入的类型身份和 pickle 全限定名。
_TransportState.__module__ = "trowel_py.codex_host.transport"
