"""花园视图的数据映射与查询编排。"""

import logging

from trowel_py.garden.repository import GardenRepository
from trowel_py.review.scheduler import get_plant_stage

logger = logging.getLogger(__name__)


def _plant_from_row(row: dict) -> dict:
    state = row.get("state")
    plant_stage = get_plant_stage(state) if state is not None else "seed"
    return {
        "card_id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "explanation": row["explanation"],
        "plant_stage": plant_stage,
        "fsrs_state": state,
        "due": row["due"],
        "reps": row["reps"] or 0,
    }


def get_plants(garden_repo: GardenRepository) -> list[dict]:
    """读取卡片与复习状态，并映射为前端使用的植物数据。"""
    rows = garden_repo.get_all_plants()
    plants = [_plant_from_row(row) for row in rows]
    logger.info("Fetched %d plants for garden", len(plants))
    return plants


def get_stats(garden_repo: GardenRepository) -> dict:
    """读取并原样返回花园聚合统计。"""
    stats = garden_repo.get_stats()
    logger.info(
        "Garden stats: %d plants, %d due, %.1f%% flowering",
        stats["total_plants"],
        stats["due_count"],
        stats["flowering_rate"],
    )
    return stats
