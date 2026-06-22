from pydantic import BaseModel, Field
from datetime import datetime
from trowel_py.pet.types import PetMood

class Pet(BaseModel):
    """
    mapping to the table pets -- the single pet owned by the default player

    Attributes:
        player_id: always 'default' (single-user system).
        mood: current mood, one of PetMood.
        hunger: satiety 0-100; below 20 the pet looks hungry.
        equipped_hat: inventory row id of the worn hat, or None when bare-headed.
        updated_at: last time any pet field changed.
    """
    player_id: str = Field(min_length=1, max_length=64)
    mood: PetMood = Field(default="normal")
    hunger: int = Field(default=80)
    equipped_hat: str | None = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.now)


class FeedRequest(BaseModel):
    """
    request body for POST /api/pet/feed.

    Attributes:
        item_id: inventory row id of the food to eat (not the catalog id).
    """
    item_id: str = Field(min_length=1)


class EquipRequest(BaseModel):
    """
    request body for PUT /api/pet/equip.

    Attributes:
        item_id: inventory row id of the hat to wear (not the catalog id).
    """
    item_id: str = Field(min_length=1)
