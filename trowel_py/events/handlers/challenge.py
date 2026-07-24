"""从复习状态中选择卡片并生成挑战事件。"""

from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState
import random

_RECENT_EXCLUDE_COUNT = 5


class ChallengeHandler:
    def can_trigger(self, state: GameState) -> bool:
        return True

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        """优先到期卡片，否则按陌生度加权；排除近期卡片后不得留下空池。"""
        all_cards = deps.card_repo.find_all()
        card_map = {card.id: card for card in all_cards}
        reviewed = {s.card_id: s for s in deps.review_repo.find_all_states()}
        recent = set(
            deps.event_repo.get_recent_card_ids("challenge", _RECENT_EXCLUDE_COUNT)
        )

        due_states = deps.review_repo.find_due(deps.now.isoformat())
        due_cards = [card_map[s.card_id] for s in due_states]
        due_cards = [card for card in due_cards if card.id not in recent] or due_cards

        if due_cards:
            chosen = deps.rng.choice(due_cards)
        else:
            pool = [card for card in all_cards if card.id not in recent] or all_cards
            weighted = [
                (card, _unfamiliarity_weight(reviewed[card.id]))
                for card in pool
                if card.id in reviewed
            ]
            if not weighted:
                weighted = [(card, 1.0) for card in pool]
            chosen = _weighted_pick(weighted, deps.rng)

        return EventResult(
            event_type="challenge",
            description=f"挑战：请描述 {chosen.title} 的核心概念",
            xp=30,
            coins=15,
            card_id=chosen.id,
        )


def _unfamiliarity_weight(state: FSRSState) -> float:
    return (state.lapses / max(state.reps, 1)) + 1


def _weighted_pick(items: list[tuple[Card, float]], rng: random.Random) -> Card:
    total = sum(weight for _, weight in items)
    remaining = rng.random() * total
    for card, weight in items:
        remaining -= weight
        if remaining <= 0:
            return card
    return items[-1][0]
