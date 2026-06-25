from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


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
