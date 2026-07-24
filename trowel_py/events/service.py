import logging
import random
from datetime import datetime

from trowel_py.cards.repository import CardRepository
from trowel_py.player.repository import PlayerRepository
from trowel_py.player.service import calculate_level
from trowel_py.review.repository import ReviewRepository
from trowel_py.events.repository import EventRepository
from trowel_py.events.engine import select_event
from trowel_py.events.config import DEFAULT_EVENT_CONFIGS
from trowel_py.events.handlers import HANDLERS
from trowel_py.events.handlers.types import EventDependencies
from trowel_py.events.rewards import distribute
from trowel_py.events.types import GameState
from trowel_py.events.models import EventLog

logger = logging.getLogger(__name__)


def build_game_state(
    player_repo: PlayerRepository,
    card_repo: CardRepository,
    review_repo: ReviewRepository,
    now: datetime,
) -> GameState:
    player = player_repo.find_or_create()
    all_cards = card_repo.find_all()
    due = review_repo.find_due(now.isoformat())
    states = review_repo.find_all_states()
    learned = tuple(s.card_id for s in states if s.reps > 0)
    return GameState(
        total_cards=len(all_cards),
        due_cards=len(due),
        player_level=calculate_level(player.xp),
        streak_days=player.streak_days,
        learned_card_ids=learned,
    )


def trigger_event(
    player_repo: PlayerRepository,
    card_repo: CardRepository,
    review_repo: ReviewRepository,
    event_repo: EventRepository,
    now: datetime,
    rng: random.Random,
) -> EventLog | None:
    state = build_game_state(player_repo, card_repo, review_repo, now)
    cooldowns = event_repo.get_last_triggered_map()
    event_type = select_event(state, DEFAULT_EVENT_CONFIGS, cooldowns, now, rng)
    if event_type is None:
        logger.info("no eligible event this turn")
        return None

    handler = HANDLERS[event_type]
    deps = EventDependencies(
        player_repo=player_repo,
        review_repo=review_repo,
        card_repo=card_repo,
        garden_repo=None,
        event_repo=event_repo,
        now=now,
        rng=rng,
    )
    if not handler.can_trigger(state):
        logger.info("event %s fired but handler declined", event_type)
        return None

    result = handler.execute(state, deps)
    log = distribute(result, player_repo, event_repo, now)
    logger.info("event %s fired: xp=%d coins=%d", event_type, result.xp, result.coins)
    return log


def get_history(event_repo: EventRepository, limit: int = 20) -> list[EventLog]:
    return event_repo.get_recent(limit)
