"""把会话问题只读快照转换成安全公开列表。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, cast

from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.window import StatisticsWindow

from .models import StoredSessionProblemPage
from .schemas import Quality, SessionProblemItemData, SessionProblemListData


class SessionProblemStatisticsReader(Protocol):
    """约束 Statistics service 使用的会话问题只读来源。"""

    def list_page(
        self,
        window: StatisticsWindow,
        *,
        limit: int,
        cursor: str | None,
    ) -> StoredSessionProblemPage:
        """返回时间窗汇总和一页非空问题。"""
        ...


def build_session_problem_list(
    reader: SessionProblemStatisticsReader,
    window: StatisticsWindow,
    *,
    limit: int = 50,
    cursor: str | None = None,
    now: datetime | None = None,
) -> SessionProblemListData:
    """校验分页并生成不公开模型 provenance 的会话问题列表。"""

    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    page = reader.list_page(window, limit=limit, cursor=cursor)
    updated_at = _parse_timestamp(page.updated_at)
    if page.reviewed_session_count == 0:
        quality: Quality = "unavailable"
    elif page.unavailable_source_count:
        quality = "partial"
    else:
        quality = "reliable"
    if page.reviewed_session_count and updated_at is None:
        quality = "partial"
    return SessionProblemListData(
        generated_at=now or datetime.now().astimezone(),
        window_start=window.start,
        window_end=window.end,
        timezone=window.timezone,
        sample_size=len(page.items),
        quality=quality,
        freshness={
            "session_problems": SourceFreshness(
                updated_at=updated_at,
                status="fresh" if updated_at is not None else "unavailable",
            )
        },
        reviewed_session_count=page.reviewed_session_count,
        problem_count=page.problem_count,
        items=[
            SessionProblemItemData(
                trowel_session_id=item.trowel_session_id,
                runtime=_runtime(item.runtime),
                closed_at=_required_timestamp(item.closed_at),
                problem_text=item.problem_text,
            )
            for item in page.items
        ],
        next_cursor=page.next_cursor,
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    """宽松解析来源新鲜度；损坏值只降低质量，不影响列表读取。"""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _required_timestamp(value: str) -> datetime:
    """解析公开行的带偏移时间，损坏记录不伪造时区。"""

    parsed = _parse_timestamp(value)
    if parsed is None:
        raise ValueError("session problem timestamp requires a UTC offset")
    return parsed


def _runtime(value: str) -> Literal["claude_code", "codex"]:
    """把数据库 runtime 收窄到公开契约闭集。"""

    if value not in {"claude_code", "codex"}:
        raise ValueError("invalid session problem runtime")
    return cast("Literal['claude_code', 'codex']", value)
