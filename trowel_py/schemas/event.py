from pydantic import BaseModel, Field
from typing import Literal as Literal
from datetime import datetime
from trowel_py.events.types import EventType


class EventLog(BaseModel):
    """``event_log`` 表对应的事件日志模型。"""

    id: str = Field(min_length=1, max_length=64)
    player_id: str = Field(min_length=1, max_length=64)
    event_type: EventType
    reward_xp: int = Field(default=0)
    reward_coin: int = Field(default=0)
    reward_item_id: str | None = Field(default=None)
    description: str | None = Field(default=None)
    card_id: str | None = Field(default=None)
    triggered_at: datetime = Field(default_factory=datetime.now)
