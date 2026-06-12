import logging
from trowel_py.garden.repository import GardenRepository
from trowel_py.review.scheduler import get_plant_stage

logger = logging.getLogger(__name__)

def get_plants(garden_repo: GardenRepository) -> list[dict]:
    """
    get origin json row -> calculate plant stage -> return to front-end
    """
    rows = garden_repo.get_all_plants()
    plants = []
    for row in rows:
        state = row.get("state")
        plant_stage = get_plant_stage(state) if state is not None else "seed"
        plants.append({
            "card_id": row["id"],
            "title": row["title"],
            "category": row["category"],
            "explanation": row["explanation"],
            "plant_stage": plant_stage,
            "fsrs_state": state,
            "due": row["due"],
            "reps": row["reps"] or 0,
        })
    logger.info("Fetched %d plants for garden", len(plants))
    return plants


def get_stats(garden_repo: GardenRepository) -> dict:
    """
    return function get_stats's result
    """
    stats = garden_repo.get_stats()
    logger.info("Garden stats: %d plants, %d due, %.1f%% flowering", stats["total_plants"], stats["due_count"], stats["flowering_rate"])
    return stats