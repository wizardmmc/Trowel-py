"""定义会话问题列表的公开 Statistics DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from trowel_py.statistics.schemas import SourceFreshness

Quality = Literal["reliable", "partial", "unavailable"]


class SessionProblemItemData(BaseModel):
    """公开一条可复制问题及其原会话身份。"""

    trowel_session_id: str
    runtime: Literal["claude_code", "codex"]
    closed_at: datetime
    problem_text: str


class SessionProblemListData(BaseModel):
    """返回一页问题、完整时间窗计数和来源状态。"""

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    timezone: str
    sample_size: int
    quality: Quality
    freshness: dict[str, SourceFreshness]
    reviewed_session_count: int
    problem_count: int
    items: list[SessionProblemItemData]
    next_cursor: str | None
