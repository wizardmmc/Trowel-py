from pydantic import BaseModel, Field
from typing import Literal
from trowel_py.schemas.card import Card
from trowel_py.schemas.extracted_card import ExtractedCard


class ExtractRequest(BaseModel):
    """提交给卡片提取流程的原始内容。"""

    content: str = Field(min_length=1)


class CardDraft(ExtractedCard):
    """尚未持久化的卡片草稿。"""

    id: str = Field(min_length=1, max_length=64)
    source: str | None = None


class ReviewRequest(BaseModel):
    """审核卡片草稿并携带可选字段修改。"""

    action: Literal["accept", "edit", "reject"]
    edits: dict | None = None  # 例如 {"title": "新标题", "difficulty": 4}


class SubmitRequest(BaseModel):
    """提交卡片复习评分。"""

    card_id: str = Field(min_length=1)
    rating: int = Field(ge=1, le=4)  # 评分值：1=Again，2=Hard，3=Good，4=Easy


class CardListResponse(BaseModel):
    """卡片分页列表。"""

    data: list[Card]
    total: int
    page: int
    limit: int
