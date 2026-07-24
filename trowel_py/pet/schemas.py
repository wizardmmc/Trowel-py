from pydantic import BaseModel, Field


class FeedRequest(BaseModel):
    """使用库存行 ID 指定要消耗的食物，而不是商品目录 ID。"""

    item_id: str = Field(min_length=1)


class EquipRequest(BaseModel):
    """使用库存行 ID 指定要装备的帽子，而不是商品目录 ID。"""

    item_id: str = Field(min_length=1)
