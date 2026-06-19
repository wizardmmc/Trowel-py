from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import EventHandler, EventDependencies, EventResult
from trowel_py.review.scheduler import get_plant_stage

class GrowthHandler:
    def can_trigger(self, state: GameState) -> bool:
        if len(state.learned_card_ids) > 0:
            return True
        else:
            return False

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        card_id = deps.rng.choice(state.learned_card_ids)
        card = deps.card_repo.find_by_id(card_id)
        fsrs_state = deps.review_repo.find_by_card_id(card_id)
        plant_stage = get_plant_stage(fsrs_state.state)
        return EventResult(
            event_type="growth",
            description=f"卡片 {card.title} 已经成长到 {plant_stage}, 再接再厉",
            xp=5,
            card_id=card_id
        )