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


class ReExplainResultSchema(BaseModel):
    """
    output contract for the 'regenerate explanation' call (021).

    Attributes:
        explanation: the newly generated natural-language explanation.
            min_length=10 mirrors ExtractedCard.explanation: a meaningful
            explanation needs at least a sentence, so we reject thin outputs.
    """
    explanation: str = Field(min_length=10)


class FollowUpMessageSchema(BaseModel):
    """
    output contract for one follow-up message (022).

    Attributes:
        role: who said it. Literal mirrors the DB check constraint
            (follow_up_messages.role in ('user','assistant')): the schema and
            the migration must agree on the allowed roles.
        content: the message text.
    """
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)