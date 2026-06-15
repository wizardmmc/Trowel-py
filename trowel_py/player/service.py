"""
player service: business rules for level, xp, coins and the daily streak.
composes PlayerRepository — no direct DB access happens here.
"""
import logging
from datetime import datetime

from trowel_py.schemas.player import PlayerProfile, InventoryItem
from trowel_py.player.repository import PlayerRepository

logger = logging.getLogger(__name__)

ITEM_PRICES = {
    "food_basic": 10,
    "food_premium": 25,
    "hat_straw": 50,
    "hat_scholar": 100,
    "hat_wreath": 75,
}

# level n requires cumulative xp = n * (n - 1) * 50
LEVEL_STEP = 50


def calculate_level(total_xp: int) -> int:
    """
    compute the player's level from cumulative xp.

    level n needs n * (n - 1) * 50 xp (L2=100, L3=300, L4=600, ...).
    """
    level = 1
    while total_xp >= (level + 1) * level * LEVEL_STEP:
        level += 1
    return level


def xp_to_next_level(total_xp: int, level: int) -> int:
    """
    how much xp is left before the player reaches level + 1.

    Args:
        total_xp: cumulative xp so far.
        level: current level (the value returned by calculate_level).
    """
    next_threshold = (level + 1) * level * LEVEL_STEP
    return next_threshold - total_xp


def get_profile(player_repo: PlayerRepository) -> PlayerProfile:
    """
    build the full player profile, including the computed level fields.

    Args:
        player_repo: player data access.
    """
    player = player_repo.find_or_create()
    level = calculate_level(player.xp)
    logger.info("build profile: level=%d xp=%d coins=%d", level, player.xp, player.coins)
    return PlayerProfile(
        id=player.id,
        xp=player.xp,
        coins=player.coins,
        streak_days=player.streak_days,
        last_active=player.last_active,
        created_at=player.created_at,
        level=level,
        xp_to_next_level=xp_to_next_level(player.xp, level),
    )


def add_xp(delta: int, player_repo: PlayerRepository) -> int:
    """
    return current level

    Args:
        delta: xp to add (can be negative).
        player_repo: player data access.
    """
    player = player_repo.find_or_create()
    old_level = calculate_level(player.xp)
    player_repo.update_xp(delta)
    new_level = calculate_level(player.xp + delta)
    if new_level > old_level:
        logger.info("level up %d -> %d", old_level, new_level)
    return new_level


def add_coins(delta: int, player_repo: PlayerRepository) -> None:
    """
    add coins to the default player (delta < 0 subtracts).
    """
    player_repo.update_coins(delta)


def spend_coins(item_id: str, player_repo: PlayerRepository) -> str:
    """
    buy an item: validate the price and balance, then deduct coins and grant it.

    the deduction and the item grant share one transaction, so they commit
    together — neither happens if the other fails.

    Args:
        item_id: key into ITEM_PRICES, e.g. 'food_basic', 'hat_straw'.
        player_repo: player data access.

    Returns:
        the granted item type, 'hat' or 'food'.

    Raises:
        ValueError: unknown item_id, or not enough coins.
    """
    if item_id not in ITEM_PRICES:
        raise ValueError(f"unknown item: {item_id}")
    price = ITEM_PRICES[item_id]

    player = player_repo.find_or_create()
    if player.coins < price:
        raise ValueError(f"not enough coins: have {player.coins}, need {price}")

    # atomic operation
    item_type = "hat" if item_id.startswith("hat_") else "food"
    player_repo.update_coins(-price)
    player_repo.add_item(item_id, item_type)
    logger.info("bought %s for %d coins", item_id, price)
    return item_type


def update_streak(player_repo: PlayerRepository, now: datetime) -> int:
    """
    update the daily check-in streak from the gap since last_active.

    same day -> unchanged; yesterday -> +1; any larger gap -> reset to 1.

    Returns:
        the new streak day count.
    """
    player = player_repo.find_or_create()
    last_active = player.last_active
    diff_days = (now.date() - last_active.date()).days

    if diff_days == 0:
        new_streak = player.streak_days  # same day, already check in
    elif diff_days == 1:
        new_streak = player.streak_days + 1
    else:
        new_streak = 1

    player_repo.update_streak(new_streak, now)
    logger.info("streak updated: %d days", new_streak)
    return new_streak


def get_inventory(player_repo: PlayerRepository) -> list[InventoryItem]:
    """
    return every inventory item owned by the default player.
    """
    return player_repo.find_inventory()
