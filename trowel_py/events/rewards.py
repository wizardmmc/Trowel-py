import logging
from datetime import datetime

from trowel_py.player.repository import PlayerRepository
from trowel_py.events.repository import EventRepository
from trowel_py.events.handlers.types import EventResult
from trowel_py.events.models import EventLog

logger = logging.getLogger(__name__)


def distribute(
    result: EventResult,
    player_repo: PlayerRepository,
    event_repo: EventRepository,
    now: datetime,
) -> EventLog:
    if result.xp:
        player_repo.update_xp(result.xp)
    if result.coins:
        player_repo.update_coins(result.coins)
    if result.item_id:
        player_repo.add_item(result.item_id, _infer_item_type(result.item_id))

    log = event_repo.record_event(
        result.event_type,
        result.description,
        result.xp,
        result.coins,
        result.item_id,
        result.card_id,
        now,
    )

    event_repo.upsert_cooldown(result.event_type, now)

    logger.info(
        "reward distributed %s xp=%d coins=%d",
        result.event_type,
        result.xp,
        result.coins,
    )
    return log


def _infer_item_type(item_id: str) -> str:
    return "hat" if item_id.startswith("hat_") else "food"
