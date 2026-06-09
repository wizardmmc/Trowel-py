from pydantic import BaseModel, Field
from typing import Literal
from trowel_py.schemas.card import Card
from trowel_py.schemas.extracted_card import ExtractedCard

class ExtractRequest(BaseModel):
    """
    pydantic model
    """
    content: str = Field(min_length=1)

class CardDraft(ExtractedCard):
    """
    pydantic model
    """
    id: str = Field(min_length=1, max_length=64)    # draft card's id
    source: str | None = None

class ReviewRequest(BaseModel):
    """
    pydantic model
    """
    action: Literal["accept", "edit", "reject"]
    edits: dict | None = None   # like {"title": "新标题", "difficulty": 4}

class CardListResponse(BaseModel):
    """
    pydantic model, all responsed cards
    """
    data: list[Card]
    total: int 
    page: int  
    limit: int 

