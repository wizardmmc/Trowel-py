"""
discovery handler: the player stumbles on a random item while exploring
"""
from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import EventHandler, EventDependencies, EventResult

# keep ids in sync with player.service.ITEM_PRICES
_DISCOVERY_ITEMS = ("food_basic", "food_premium", "hat_straw", "hat_scholar", "hat_wreath")

class DiscoveryHandler:
    def can_trigger(self, state: GameState) -> bool:
        return True # engine's cooldown/min_cards is the only filter
    
    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        item_id = deps.rng.choice(_DISCOVERY_ITEMS)
        return EventResult(
            event_type="discovery",
            description=f"你在角落里发现了一个{item_id}",
            xp=10,
            item_id=item_id
        )