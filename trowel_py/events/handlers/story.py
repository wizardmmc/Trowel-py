from typing import TYPE_CHECKING, cast

from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)

if TYPE_CHECKING:
    from trowel_py.cards.models import Card


class StoryHandler:
    def can_trigger(self, state: GameState) -> bool:
        if len(state.learned_card_ids) > 0:
            return True
        else:
            return False

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        card_id = deps.rng.choice(state.learned_card_ids)
        # learned_card_ids 来自受外键约束的 FSRS 行，因此对应卡片必然存在。
        card = cast("Card", deps.card_repo.find_by_id(card_id))
        return EventResult(
            event_type="story",
            description=f"还记得 {card.title} 这个知识点吗？ {card.explanation}",
            xp=5,
            card_id=card_id,
        )
