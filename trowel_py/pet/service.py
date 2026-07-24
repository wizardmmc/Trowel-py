import logging
import random
from typing import Literal

from trowel_py.pet.brain import PetBrain, PetBrainInput
from trowel_py.pet.repository import PetRepository
from trowel_py.pet.types import MoodTrigger, PetMood
from trowel_py.player.repository import PlayerRepository
from trowel_py.pet.models import Pet
from trowel_py.player.models import InventoryItem

logger = logging.getLogger(__name__)

HUNGER_MAX = 100
HUNGER_DECAY_PER_HOUR = 2

_MOOD_TRANSITIONS: dict[MoodTrigger, PetMood] = {
    "review_correct": "happy",
    "review_complete": "excited",
    "event_trigger": "excited",
    "interaction": "happy",
    "hunger_low": "normal",
    "idle": "normal",
    "feynman_trigger": "curious",
}

_FOOD_RECOVERY: dict[str, int] = {
    "food_basic": 20,
    "food_premium": 50,
}


def _require_inventory_item(
    item_id: str,
    expected_type: Literal["food", "hat"],
    player_repo: PlayerRepository,
) -> InventoryItem:
    row = player_repo.find_item_by_id(item_id)
    if row is None:
        raise ValueError(f"item {item_id} not in inventory")
    if row.item_type != expected_type:
        expected_label = "a hat" if expected_type == "hat" else "food"
        raise ValueError(f"item {item_id} is not {expected_label}")
    return row


def resolve_mood(trigger: MoodTrigger) -> PetMood:
    return _MOOD_TRANSITIONS[trigger]


def _clamp_hunger(value: int) -> int:
    return max(0, min(value, HUNGER_MAX))


def get_pet(pet_repo: PetRepository) -> Pet:
    return pet_repo.find_or_create()


def feed(item_id: str, pet_repo: PetRepository, player_repo: PlayerRepository) -> Pet:
    """`item_id` 是库存行 ID；恢复量由该行的商品目录 ID 决定。"""
    row = _require_inventory_item(item_id, "food", player_repo)

    recovery = _FOOD_RECOVERY.get(row.item_id)
    if recovery is None:
        raise ValueError(f"unknown food {row.item_id}")

    pet = pet_repo.find_or_create()
    new_hunger = _clamp_hunger(pet.hunger + recovery)

    player_repo.remove_item(row.id)
    pet_repo.update_hunger(new_hunger)
    logger.info("fed pet %s: hunger %d -> %d", row.item_id, pet.hunger, new_hunger)
    return pet_repo.find_or_create()


def interact(pet_repo: PetRepository, brain: PetBrain, rng: random.Random) -> dict:
    new_mood = resolve_mood("interaction")
    pet_repo.update_mood(new_mood)
    response = brain.generate_response(PetBrainInput(mood=new_mood), rng.random())
    logger.info("interacted: pet says %r", response.text)
    return {
        "response": response,
        "pet": pet_repo.find_or_create(),
    }


def equip_hat(
    item_id: str, pet_repo: PetRepository, player_repo: PlayerRepository
) -> Pet:
    """`item_id` 是库存行 ID；库存装备状态与宠物引用必须同步。"""
    row = _require_inventory_item(item_id, "hat", player_repo)

    # 调用方须用同一连接组装两个仓储，三步写入才能作为一个事务提交。
    player_repo.unequip_all_hats()
    player_repo.set_equipped(row.id, 1)
    pet_repo.update_equipped_hat(row.id)
    logger.info("equipped hat %s", row.item_id)
    return pet_repo.find_or_create()


def update_mood(trigger: MoodTrigger, pet_repo: PetRepository) -> Pet:
    new_mood = resolve_mood(trigger)
    pet_repo.update_mood(new_mood)
    return pet_repo.find_or_create()


def tick_hunger(
    pet_repo: PetRepository,
    elapsed_minutes: int,
    decay_per_hour: int = HUNGER_DECAY_PER_HOUR,
) -> Pet:
    """只衰减饥饿值；“饥饿”是展示层派生状态，不写入 `mood`。"""
    pet = pet_repo.find_or_create()
    decay = int(elapsed_minutes * decay_per_hour / 60)
    new_hunger = _clamp_hunger(pet.hunger - decay)
    pet_repo.update_hunger(new_hunger)
    return pet_repo.find_or_create()
