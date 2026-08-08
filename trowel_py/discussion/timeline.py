"""提供 discussion participant 单轮原生轨迹的只读查询。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Callable

from trowel_py.discussion.errors import DiscussionRuntimeError
from trowel_py.discussion.participant_sessions import ParticipantHistoryPort
from trowel_py.discussion.repository import DiscussionRepository

RepositoryOpener = Callable[[], AbstractContextManager[DiscussionRepository]]


class DiscussionTimelineService:
    """把 attempt 归属校验与原生历史读取组合成稳定公开 DTO。"""

    def __init__(
        self,
        repository_opener: RepositoryOpener,
        history: ParticipantHistoryPort,
    ) -> None:
        """装配短连接 repository 和只读历史端口。"""

        self._open_repository = repository_opener
        self._history = history

    async def get_attempt(
        self,
        discussion_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """读取一个 attempt 的事件；不可恢复时返回明确降级状态。

        Args:
            discussion_id: 当前研讨 ID。
            attempt_id: participant 槽位公开的 current attempt ID。

        Returns:
            attempt 身份、状态、可用性和按原生顺序排列的 AgentEvent。
        """

        with self._open_repository() as repository:
            request = repository.get_attempt_history_request(
                discussion_id,
                attempt_id,
            )
        try:
            events = await self._history.read_attempt_history(request)
        except DiscussionRuntimeError:
            events = []
        return {
            "attempt_id": request.id,
            "participant_id": request.participant_id,
            "round_number": request.round_number,
            "runtime": request.runtime.value,
            "status": request.status,
            "availability": "available" if events else "unavailable",
            "events": events,
        }
