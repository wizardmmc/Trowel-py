"""定义 Agent Host 统计和关闭两种原生会话时使用的共同接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from trowel_py.agent_host.binding import Runtime, SessionBinding


@dataclass(frozen=True)
class RuntimeLiveState:
    """记录一个原生会话当前是否连接以及是否仍有未结束轮次。

    Attributes:
        connected: 原生会话是否仍登记且可由对应 runtime 找到。
        has_in_flight_turn: runtime 是否仍在启动、处理或收尾一个轮次。
    """

    connected: bool
    has_in_flight_turn: bool

    @classmethod
    def disconnected(cls) -> RuntimeLiveState:
        """返回没有原生会话时的统一状态。"""

        return cls(connected=False, has_in_flight_turn=False)


@dataclass(frozen=True)
class RuntimeCloseResult:
    """说明 runtime 是否已经核验指定 session 的临时资源归零。

    Attributes:
        status: `closed` 表示资源已经归零；`needs_reconcile` 表示 binding 必须保留
            供本次重试或下次启动继续清理。
        remaining_resource_count: 尚未确认关闭的资源数量。
        remaining_resource_kinds: 尚未关闭的资源类型，按名称排序且去重。
        error: 不含提示正文、路径或凭据的失败说明；成功时为 None。
    """

    status: Literal["closed", "needs_reconcile"]
    remaining_resource_count: int
    remaining_resource_kinds: tuple[str, ...]
    error: str | None = None

    @property
    def is_closed(self) -> bool:
        """返回本次关闭是否已经核验资源归零。"""

        return self.status == "closed" and self.remaining_resource_count == 0

    @classmethod
    def closed(cls) -> RuntimeCloseResult:
        """构造没有剩余资源的成功结果。"""

        return cls(
            status="closed",
            remaining_resource_count=0,
            remaining_resource_kinds=(),
        )

    @classmethod
    def needs_reconcile(
        cls,
        *,
        remaining_resource_count: int,
        remaining_resource_kinds: tuple[str, ...],
        error: str,
    ) -> RuntimeCloseResult:
        """构造需要保留 binding 继续收敛的结果。

        Args:
            remaining_resource_count: 尚未确认关闭的资源数量。
            remaining_resource_kinds: 尚未关闭的资源类型。
            error: 去敏后的失败说明。
        """

        return cls(
            status="needs_reconcile",
            remaining_resource_count=remaining_resource_count,
            remaining_resource_kinds=remaining_resource_kinds,
            error=error,
        )


class RuntimeSessionPort(Protocol):
    """规定容量管理和统一删除所需的最小运行时能力。"""

    runtime: Runtime

    def session_ids(self) -> tuple[str, ...]:
        """返回当前 runtime 登记的全部 Trowel 会话 ID。"""

        ...

    def live_state(self, session_id: str) -> RuntimeLiveState:
        """返回指定 Trowel 会话的实时连接和轮次状态。"""

        ...

    async def close(self, binding: SessionBinding) -> RuntimeCloseResult:
        """收敛 binding 对应的原生资源，并返回资源核验结果。"""

        ...

    def abort_create(self, session_id: str) -> None:
        """撤销尚未完成 binding 提交的运行时会话创建。"""

        ...
