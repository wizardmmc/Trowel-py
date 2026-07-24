from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class Card(BaseModel):
    """``cards`` 表对应的卡片模型。"""

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    example: str | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Literal["active", "archived", "draft"] = "active"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
