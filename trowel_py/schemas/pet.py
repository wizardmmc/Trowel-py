from pydantic import BaseModel, Field
from datetime import datetime
from trowel_py.pet.types import PetMood


class Pet(BaseModel):
    """默认玩家的宠物状态；饱食度低于 20 时展示为饥饿，帽子字段保存库存行 ID。"""

    player_id: str = Field(min_length=1, max_length=64)
    mood: PetMood = Field(default="normal")
    hunger: int = Field(default=80)
    equipped_hat: str | None = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.now)


class FeedRequest(BaseModel):
    """使用库存行 ID 指定要消耗的食物，而不是商品目录 ID。"""

    item_id: str = Field(min_length=1)


class EquipRequest(BaseModel):
    """使用库存行 ID 指定要装备的帽子，而不是商品目录 ID。"""

    item_id: str = Field(min_length=1)
