from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class Player(BaseModel):
    """``players`` 表对应的玩家模型。"""

    id: str = Field(min_length=1, max_length=64)
    xp: int = Field(default=0)
    coins: int = Field(default=0)
    streak_days: int = Field(default=0)
    last_active: datetime = Field(default_factory=datetime.now)
    created_at: datetime = Field(default_factory=datetime.now)


class PlayerProfile(Player):
    level: int = Field(default=1)
    xp_to_next_level: int


class InventoryItem(BaseModel):
    """``inventory`` 表对应的库存行模型。"""

    id: str = Field(min_length=1, max_length=64)
    player_id: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1)
    item_type: Literal["hat", "food"] = Field(default="food")
    equipped: int = Field(default=0)
    obtained_at: datetime = Field(default_factory=datetime.now)


class BuyItemRequest(BaseModel):
    item_id: str = Field(min_length=1)
