from pydantic import BaseModel, Field
from typing import Literal


class ExtractedCard(BaseModel):
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    example: str | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    tags: list[str]
    confidence: int = Field(default=3, ge=1, le=5)
    source_type: Literal["chat", "git_diff", "cli", "general"]


class ExtractOutput(BaseModel):
    cards: list[ExtractedCard]
