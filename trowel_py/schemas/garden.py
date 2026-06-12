from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime
from trowel_py.schemas.card import Card

class PlantInfo(BaseModel):
    card_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    plant_stage: Literal["seed", "sprout", "tree", "wilting"] = "seed"
    fsrs_state: Literal[0, 1, 2, 3] | None = None # 0:new, 1:learning, 2:review, 3:relearning
    due: str | None = None
    reps: int = Field(default=0)

class GardenStats(BaseModel):
    """
    aggregate statistics
    """
    total_plants: int = 0
    due_count: int = 0  # number of cards waited to due
    flowering_rate: float = 0.0 # tree num / total num
