from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class FSRSState(BaseModel):
    card_id: str = Field(min_length=1)  # references cards(id)
    stability: float = Field(default=0.0)  # memory stability
    difficulty: float = Field(default=0.0)
    elapsed_days: int = Field(default=0)  # days since last review
    scheduled_days: int = Field(default=0)  # days until next review
    reps: int = Field(default=0)  # number of reviews
    lapses: int = Field(default=0)  # times user chose the forget button
    state: Literal[0, 1, 2, 3] = 0  # 0:new, 1:learning, 2:review, 3:relearning
    due: datetime = Field(default_factory=datetime.now)  # next review time
    last_review: datetime | None = None  # None until first review


class ReviewLog(BaseModel):
    id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)  # references cards(id)
    rating: Literal[1, 2, 3, 4]  # 1:again, 2:hard, 3:good, 4:easy
    state: Literal[0, 1, 2, 3]  # card state at review time
    elapsed_days: int = Field(default=0)
    scheduled_days: int = Field(default=0)
    duration_ms: int | None = None  # review duration in milliseconds
    created_at: datetime = Field(default_factory=datetime.now)
