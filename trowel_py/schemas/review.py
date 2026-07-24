from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime

FSRSStateCode = Literal[0, 1, 2, 3]
ReviewRating = Literal[1, 2, 3, 4]


class FSRSState(BaseModel):
    """卡片的 FSRS 复习状态。"""

    card_id: str = Field(min_length=1)  # 引用 cards(id)
    stability: float = Field(default=0.0)  # 记忆稳定性
    difficulty: float = Field(default=0.0)
    elapsed_days: int = Field(default=0)  # 距上次复习的天数
    scheduled_days: int = Field(default=0)  # 距下次复习的天数
    reps: int = Field(default=0)  # 累计复习次数
    lapses: int = Field(default=0)  # 选择“忘记”的累计次数
    state: FSRSStateCode = 0  # 0=新卡，1=学习中，2=复习，3=重学
    due: datetime = Field(default_factory=datetime.now)  # 下次复习时间
    last_review: datetime | None = None  # 首次复习前为 None


class ReviewLog(BaseModel):
    """单次复习记录。"""

    id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)  # 引用 cards(id)
    rating: ReviewRating  # 1=重来，2=困难，3=良好，4=简单
    state: FSRSStateCode  # 本次复习时的卡片状态
    elapsed_days: int = Field(default=0)
    scheduled_days: int = Field(default=0)
    duration_ms: int | None = None  # 复习耗时，单位为毫秒
    created_at: datetime = Field(default_factory=datetime.now)
