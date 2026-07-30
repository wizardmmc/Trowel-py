"""定义 Agent Host 统计和关闭两种原生会话时使用的共同接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trowel_py.agent_host.binding import Runtime


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


class RuntimeSessionPort(Protocol):
    """规定容量管理和统一删除所需的最小运行时能力。"""

    runtime: Runtime

    def session_ids(self) -> tuple[str, ...]:
        """返回当前 runtime 登记的全部 Trowel 会话 ID。"""

        ...

    def live_state(self, session_id: str) -> RuntimeLiveState:
        """返回指定 Trowel 会话的实时连接和轮次状态。"""

        ...

    async def close(self, session_id: str) -> None:
        """关闭或注销指定 Trowel 会话的进程内运行状态。"""

        ...

    def abort_create(self, session_id: str) -> None:
        """撤销尚未完成 binding 提交的运行时会话创建。"""

        ...
