import logging
from datetime import datetime

from trowel_py.player.models import PlayerProfile, InventoryItem
from trowel_py.player.repository import PlayerRepository

logger = logging.getLogger(__name__)

ITEM_PRICES = {
    "food_basic": 10,
    "food_premium": 25,
    "hat_straw": 50,
    "hat_scholar": 100,
    "hat_wreath": 75,
}

LEVEL_STEP = 50


def calculate_level(total_xp: int) -> int:
    """等级 n 的累计经验门槛为 ``n * (n - 1) * 50``。"""
    level = 1
    while total_xp >= (level + 1) * level * LEVEL_STEP:
        level += 1
    return level


def xp_to_next_level(total_xp: int, level: int) -> int:
    next_threshold = (level + 1) * level * LEVEL_STEP
    return next_threshold - total_xp


def get_profile(player_repo: PlayerRepository) -> PlayerProfile:
    player = player_repo.find_or_create()
    level = calculate_level(player.xp)
    logger.info(
        "build profile: level=%d xp=%d coins=%d", level, player.xp, player.coins
    )
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
    """``delta`` 可为负数；返回变更后的等级。"""
    player = player_repo.find_or_create()
    old_level = calculate_level(player.xp)
    player_repo.update_xp(delta)
    new_level = calculate_level(player.xp + delta)
    if new_level > old_level:
        logger.info("level up %d -> %d", old_level, new_level)
    return new_level


def add_coins(delta: int, player_repo: PlayerRepository) -> None:
    """``delta`` 为负数时扣除金币。"""
    player_repo.update_coins(delta)


def spend_coins(item_id: str, player_repo: PlayerRepository) -> str:
    """商品不存在或余额不足时抛出 ``ValueError``。"""
    if item_id not in ITEM_PRICES:
        raise ValueError(f"unknown item: {item_id}")
    price = ITEM_PRICES[item_id]

    player = player_repo.find_or_create()
    if player.coins < price:
        raise ValueError(f"not enough coins: have {player.coins}, need {price}")

    item_type = "hat" if item_id.startswith("hat_") else "food"
    player_repo.update_coins(-price)
    player_repo.add_item(item_id, item_type)
    logger.info("bought %s for %d coins", item_id, price)
    return item_type


def update_streak(player_repo: PlayerRepository, now: datetime) -> int:
    """同日保持不变，相隔一天加一，间隔更久则重置为一天。"""
    player = player_repo.find_or_create()
    last_active = player.last_active
    diff_days = (now.date() - last_active.date()).days

    if diff_days == 0:
        new_streak = player.streak_days
    elif diff_days == 1:
        new_streak = player.streak_days + 1
    else:
        new_streak = 1

    player_repo.update_streak(new_streak, now)
    logger.info("streak updated: %d days", new_streak)
    return new_streak


def get_inventory(player_repo: PlayerRepository) -> list[InventoryItem]:
    return player_repo.find_inventory()
