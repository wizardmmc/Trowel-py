import logging
import random

from trowel_py.pet.repository import PetRepository
from trowel_py.player.repository import PlayerRepository
from trowel_py.pet.brain import PetBrain, PetBrainInput
from trowel_py.schemas.pet import Pet
from trowel_py.pet.types import MoodTrigger, PetMood

logger = logging.getLogger(__name__)

HUNGER_MAX = 100
# HUNGER_LOW_THRESHOLD = 20
HUNGER_DECAY_PER_HOUR = 2  # spec: 2 points lost per hour naturally

# mood transition table
_MOOD_TRANSITIONS: dict[MoodTrigger, PetMood] = {
    "review_correct": "happy",
    "review_complete": "excited",
    "event_trigger": "excited",
    "interaction": "happy",
    "hunger_low": "normal",
    "idle": "normal",
    "feynman_trigger": "curious",
}

# how much each food restores. unknown catalog id -> ValueError (no silent junk food).
_FOOD_RECOVERY: dict[str, int] = {
    "food_basic": 20,
    "food_premium": 50,
}


def resolve_mood(trigger: MoodTrigger) -> PetMood:
    """
    pure: map a mood trigger to the mood it produces.

    Args:
        trigger: what happened to the pet.

    Returns:
        the resulting mood.
    """
    return _MOOD_TRANSITIONS[trigger]


def _clamp_hunger(value: int) -> int:
    """keep hunger inside [0, HUNGER_MAX]."""
    return max(0, min(value, HUNGER_MAX))


def get_pet(pet_repo: PetRepository) -> Pet:
    """
    return the pet's current state.
    """
    return pet_repo.find_or_create()

def feed(item_id: str, pet_repo: PetRepository, player_repo: PlayerRepository) -> Pet:
    """
    feed the pet one food item: consume it from inventory, restore hunger.

    Args:
        item_id: inventory row id of the food (NOT the catalog id).
        pet_repo: pet data access.
        player_repo: inventory data access.

    Returns:
        the updated pet.

    Raises:
        ValueError: item not in inventory, or not food, or unknown food catalog id.
    """
    row = player_repo.find_item_by_id(item_id)  # item_id arg = inventory row id
    if row is None:
        raise ValueError(f"item {item_id} not in inventory")
    if row.item_type != "food":
        raise ValueError(f"item {item_id} is not food")

    recovery = _FOOD_RECOVERY.get(row.item_id)  # row.item_id = catalog id (food_basic..)
    if recovery is None:
        raise ValueError(f"unknown food {row.item_id}")

    pet = pet_repo.find_or_create()
    new_hunger = _clamp_hunger(pet.hunger + recovery)

    player_repo.remove_item(row.id)
    pet_repo.update_hunger(new_hunger)
    logger.info("fed pet %s: hunger %d -> %d", row.item_id, pet.hunger, new_hunger)
    return pet_repo.find_or_create()

def interact(pet_repo: PetRepository, brain: PetBrain, rng: random.Random) -> dict:
    """
    pet the pet: set mood happy, then ask the brain for one spoken line.

    Args:
        pet_repo: pet data access.
        brain: the personality that produces the spoken line.
        rng: randomness source for picking a template line.

    Returns:
        {"response": PetResponse, "pet": updated Pet}.
    """
    new_mood = resolve_mood("interaction")  # -> happy
    pet_repo.update_mood(new_mood)
    response = brain.generate_response(PetBrainInput(mood=new_mood), rng.random())
    logger.info("interacted: pet says %r", response.text)
    return {
        "response": response,
        "pet": pet_repo.find_or_create(),
    }

def equip_hat(item_id: str, pet_repo: PetRepository, player_repo: PlayerRepository) -> Pet:
    """
    equip a hat: clear all hats, wear this one, sync the pet's equipped_hat.

    Args:
        item_id: inventory row id of the hat (NOT the catalog id).
        pet_repo: pet data access.
        player_repo: inventory data access.

    Returns:
        the updated pet.

    Raises:
        ValueError: item not in inventory, or not a hat.
    """
    row = player_repo.find_item_by_id(item_id)
    if row is None:
        raise ValueError(f"item {item_id} not in inventory")
    if row.item_type != "hat":
        raise ValueError(f"item {item_id} is not a hat")

    # 3-step orchestration on the same conn => atomic, "one hat at a time" enforced.
    player_repo.unequip_all_hats()
    player_repo.set_equipped(row.id, 1)
    pet_repo.update_equipped_hat(row.id)
    logger.info("equipped hat %s", row.item_id)
    return pet_repo.find_or_create()

def update_mood(trigger: MoodTrigger, pet_repo: PetRepository) -> Pet:
    """
    apply a mood transition and persist it.

    Args:
        trigger: what caused the mood change.
        pet_repo: pet data access.

    Returns:
        the updated pet.
    """
    new_mood = resolve_mood(trigger)
    pet_repo.update_mood(new_mood)
    return pet_repo.find_or_create()

def tick_hunger(
    pet_repo: PetRepository,
    elapsed_minutes: int,
    decay_per_hour: int = HUNGER_DECAY_PER_HOUR,
) -> Pet:
    """
    apply natural hunger decay for elapsed_minutes.

    not wired to any timer in this slice (SSE/timer comes in 016); this is the
    logic a future timer would call. only touches hunger — "hungry" is a derived
    display state (hunger < threshold), never stored in pets.mood.

    Args:
        pet_repo: pet data access.
        elapsed_minutes: how much time passed since the last tick.
        decay_per_hour: hunger points lost per hour (configurable).

    Returns:
        the updated pet.
    """
    pet = pet_repo.find_or_create()
    decay = int(elapsed_minutes * decay_per_hour / 60)
    new_hunger = _clamp_hunger(pet.hunger - decay)
    pet_repo.update_hunger(new_hunger)
    return pet_repo.find_or_create()
