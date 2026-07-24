from typing import Literal

from pydantic import BaseModel, Field

from trowel_py.cards.models import Card as Card


class PlantInfo(BaseModel):
    card_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    plant_stage: Literal["seed", "sprout", "tree", "wilting"] = "seed"
    fsrs_state: Literal[0, 1, 2, 3] | None = (
        None  # FSRS 状态：0=New，1=Learning，2=Review，3=Relearning
    )
    due: str | None = None
    reps: int = Field(default=0)


class GardenStats(BaseModel):
    """花园聚合统计。"""

    total_plants: int = 0
    due_count: int = 0
    flowering_rate: float = 0.0
