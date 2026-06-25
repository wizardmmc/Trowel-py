from __future__ import annotations
from pydantic import BaseModel, Field


class ReExplainResultSchema(BaseModel):
    """
    output contract for the 'regenerate explanation' call (021).

    Attributes:
        explanation: the newly generated natural-language explanation.
            min_length=10 mirrors ExtractedCard.explanation: a meaningful
            explanation needs at least a sentence, so we reject thin outputs.
    """
    explanation: str = Field(min_length=10)