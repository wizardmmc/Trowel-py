"""玩家购买接口接收的请求数据。"""

from pydantic import BaseModel, Field


class BuyItemRequest(BaseModel):
    """指定要购买的商品目录 ID。"""

    item_id: str = Field(min_length=1)
