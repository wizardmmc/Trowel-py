"""
business logic layer, encapsulate repository layer and scheduler layer
"""
import logging
from datetime import datetime, timezone

from trowel_py.schemas.review import FSRSState
from trowel_py.review.repository import ReviewRepository
from trowel_py.cards.repository import CardRepository
from trowel_py.review.scheduler import get_plant_stage, schedule_review

logger = logging.getLogger(__name__)

def get_due_cards(review_repo: ReviewRepository, card_repo: CardRepository) -> list[dict]:
    """
    fetch all cards that are due for review, sorted by due date.

    return component data
    """
    now = datetime.now(timezone.utc).isoformat()    # convert to string
    due_states = review_repo.find_due(now)

    results = []
    for state in due_states:
        card = card_repo.find_by_id(state.card_id)
        if card is None:
            logger.warning("FSRS state references missing card: %s", state.card_id)
            continue
        results.append({
            "card": card,
            "fsrs_state": state,
            "plant_stage": get_plant_stage(state.state),
        })
    return results

def submit_review(
        card_id: str,
        rating: int,
        review_repo: ReviewRepository,
        card_repo: CardRepository
) -> dict | None:
    # validate card exists
    card = card_repo.find_by_id(card_id)
    if card is None:
        logger.warning("Submit review for unknown card: %s", card_id)
        return None
    
    # get current FSRS state
    state = review_repo.find_by_card_id(card_id)
    if state is None:
        logger.error("No FSRS state for card: %s", card_id)
        return None
    
    # compute next state via scheduler
    now = datetime.now(timezone.utc)
    new_state, review_log = schedule_review(state, rating, now)

    # persist both
    review_repo.update_fsrs_state(new_state)
    review_repo.save_review_log(review_log)

    # return result
    return {
        "card": card,
        "fsrs_state": new_state,
        "review_log": review_log,
        "plant_stage": get_plant_stage(new_state.state),
        "plant_changed": get_plant_stage(new_state.state) != get_plant_stage(state.state)
    }

def get_session_stats(review_repo: ReviewRepository, since: str) -> dict:
    """
    get aggregated review statistics since a given timestamp
    """
    return review_repo.get_session_stats(since)

def get_review_stats(review_repo: ReviewRepository) -> dict:
    """
    get overall review statistics(all time).
    """
    return review_repo.get_session_stats("2000-01-01T00:00:00")