"""保存 Codex app-server 连接的待响应请求和逻辑关闭状态。

``_TransportState`` 仍以 ``trowel_py.codex_host.transport`` 为模块名，并由该模块
继续导出，以兼容既有私有导入和 pickle 全限定名。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

JsonObject = dict[str, Any]


@dataclass
class _TransportState:
    """管理请求 ID 到响应 Future 的登记，以及连接关闭转换。

    Attributes:
        pending: 客户端请求 ID 到响应 Future 的映射。
        closing: 是否已开始主动关闭；用于保证关闭序列只执行一次。
        failed: 是否已执行 pending 清理；主动关闭和 reader 结束都会置为 True。
    """

    pending: dict[str, asyncio.Future[JsonObject]] = field(default_factory=dict)
    closing: bool = False
    failed: bool = False

    @property
    def closed(self) -> bool:
        """返回连接是否已经进入关闭或不可用状态。"""

        return self.closing or self.failed

    def begin_closing(self) -> bool:
        """首次将 ``closing`` 置为 True，并报告是否发生了这次转换。

        ``failed`` 不影响返回值；即使连接已因 reader 失效而关闭，首次调用仍会把
        ``closing`` 置为 True。
        """

        if self.closing:
            return False
        self.closing = True
        return True

    def register(self, request_id: str, future: asyncio.Future[JsonObject]) -> None:
        """登记一个客户端请求及其响应 Future。

        Args:
            request_id: 用于匹配 JSON-RPC 响应的客户端请求 ID。
            future: 收到结果、错误或连接关闭时完成的等待对象。
        """

        self.pending[request_id] = future

    def pop(self, request_id: str) -> asyncio.Future[JsonObject] | None:
        """取出并移除请求的响应 Future；ID 未登记时返回 None。"""

        return self.pending.pop(request_id, None)

    def discard(self, request_id: str) -> None:
        """移除请求的响应 Future；ID 未登记时不执行操作。"""

        self.pending.pop(request_id, None)

    def fail_all(self, error: BaseException) -> None:
        """清空全部 pending，并为其中未完成的 Future 设置同一个异常。

        先从登记表中移除当前全部 Future，再逐个为其中未完成的 Future 设置异常；
        已完成的 Future 保持原状态。主动关闭和 reader 结束都会执行此转换。

        Args:
            error: 设置给每个未完成响应 Future 的连接错误。
        """

        pending = self.pending
        self.pending = {}
        self.failed = True
        for future in pending.values():
            if not future.done():
                future.set_exception(error)


# 固定 pickle 和类型诊断使用的模块名；transport 模块另行继续导出该类型。
_TransportState.__module__ = "trowel_py.codex_host.transport"
