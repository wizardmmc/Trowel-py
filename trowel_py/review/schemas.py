from pydantic import BaseModel, Field


class SubmitRequest(BaseModel):
    """提交卡片复习评分。"""

    card_id: str = Field(min_length=1)
    rating: int = Field(ge=1, le=4)  # 评分值：1=Again，2=Hard，3=Good，4=Easy
