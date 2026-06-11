"""Review API routes — HTTP endpoints for the FSRS review system."""
from fastapi import APIRouter, Depends
from trowel_py.review.service import get_due_cards, submit_review, get_session_stats, get_review_stats
from trowel_py.db.connection import create_db
from trowel_py.cards.repository import CardRepository, create_card_repository
from trowel_py.review.repository import ReviewRepository, create_review_repository
from trowel_py.schemas.api import SubmitRequest
import logging
import sqlite3

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_conn() -> sqlite3.Connection:
    return create_db()


def _get_card_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> CardRepository:
    return create_card_repository(conn)


def _get_review_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> ReviewRepository:
    return create_review_repository(conn)


@router.get("/due")
def due(
    card_repo: CardRepository = Depends(_get_card_repo),
    review_repo: ReviewRepository = Depends(_get_review_repo),
) -> dict:
    """Fetch all cards that are due for review, sorted by due date."""
    results = get_due_cards(review_repo, card_repo)
    logger.info("Fetched %d due cards", len(results))

    res = []
    for result in results:
        res.append({
            "card": result["card"].model_dump(),
            "fsrs_state": result["fsrs_state"].model_dump(),
            "plant_stage": result["plant_stage"],
        })

    return {"success": True, "data": res, "error": None}


@router.post("/submit")
def submit(
    request: SubmitRequest,
    card_repo: CardRepository = Depends(_get_card_repo),
    review_repo: ReviewRepository = Depends(_get_review_repo),
) -> dict:
    """Submit a review rating for a card (1=Again, 2=Hard, 3=Good, 4=Easy)."""
    logger.info("Submit review for card %s, rating=%d", request.card_id, request.rating)
    result = submit_review(request.card_id, request.rating, review_repo, card_repo)

    if result is None:
        logger.warning("Submit review failed: card %s not found", request.card_id)
        return {"success": False, "data": None, "error": "Card not found"}

    return {
        "success": True,
        "data": {
            "card": result["card"].model_dump(),
            "fsrs_state": result["fsrs_state"].model_dump(),
            "review_log": result["review_log"].model_dump(),
            "plant_stage": result["plant_stage"],
            "plant_changed": result["plant_changed"],
        },
        "error": None,
    }


@router.get("/session-stats")
def session_stats(
    since: str,
    review_repo: ReviewRepository = Depends(_get_review_repo),
) -> dict:
    """Get aggregated review statistics since a given ISO timestamp."""
    logger.info("Session stats request since: %s", since)
    stats = get_session_stats(review_repo, since)
    return {"success": True, "data": stats, "error": None}


@router.get("/stats")
def stats(
    review_repo: ReviewRepository = Depends(_get_review_repo),
) -> dict:
    """Get overall review statistics (all time)."""
    logger.info("Overall stats request")
    stats = get_review_stats(review_repo)
    return {"success": True, "data": stats, "error": None}
