from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class FeynmanQuestionSchema(BaseModel):
    """
    output contract for the 'LLM poses a question' call (019).

    Attributes:
        question: the question the model asks about the card.
        hint: an optional nudge; not every question needs one.
    """

    question: str = Field(min_length=1)
    hint: str | None = None


class FeynmanEvaluationSchema(BaseModel):
    """
    output contract for the 'llm judges the answer' call
    """

    accuracy: int = Field(ge=0, le=100)
    completeness: int = Field(ge=0, le=100)
    feedback: str = Field(min_length=1)
    missed_points: list[str] = Field(default_factory=list)
