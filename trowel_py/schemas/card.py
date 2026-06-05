from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime

# map table cards into python data structure, and describe type check and type hint
class Card(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    example: str| None = None   # optional field
    difficulty: int = Field(default=3, ge=1, le=5)
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Literal["active", "archived", "draft"] = "active"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

