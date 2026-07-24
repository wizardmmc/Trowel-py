from pydantic import BaseModel, Field


class BuyItemRequest(BaseModel):
    item_id: str = Field(min_length=1)
