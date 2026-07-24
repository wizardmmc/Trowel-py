from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)

_GIFT_ITEMS = ("food_basic", "food_premium", "hat_straw")


class GiftHandler:
    def can_trigger(self, state: GameState) -> bool:
        return True

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        item_id = deps.rng.choice(_GIFT_ITEMS)
        return EventResult(
            event_type="gift",
            description=f"宠物送了你一个 {item_id}",
            xp=10,
            item_id=item_id,
        )
