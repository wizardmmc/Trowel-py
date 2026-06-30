from __future__ import annotations

from pydantic import BaseModel, Field


class ReExplainRequest(BaseModel):
    """
    request body for POST /cards/re-explain (slice 021).

    The endpoint is a stateless generator that runs during card *review*,
    before the card is persisted — so it takes the draft's content directly,
    not a card_id (the draft has no id in the DB yet).

    Attributes:
        explanation: the current explanation to improve (V0 or a prior candidate).
        title: the card title, gives the LLM context.
        category: the card category, gives the LLM context.
        user_hint: optional direction/feeling from the user ("make it more
            concrete", etc). None means "regenerate freely".
    """

    explanation: str = Field(min_length=10)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    user_hint: str | None = None


class ReExplainResultSchema(BaseModel):
    """
    output contract for the 'regenerate explanation' call (021).

    Attributes:
        explanation: the newly generated natural-language explanation.
            min_length=10 mirrors ExtractedCard.explanation: a meaningful
            explanation needs at least a sentence, so we reject thin outputs.
    """

    explanation: str = Field(min_length=10)
