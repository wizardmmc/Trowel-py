from fastapi import APIRouter, Depends
from trowel_py.db.connection import create_db
from trowel_py.db.connection import create_db
from trowel_py.cards.repository import create_card_repository
from trowel_py.player.repository import create_player_repository
from trowel_py.review.repository import create_review_repository
from trowel_py.events.repository import create_event_repository
from trowel_py.events.service import trigger_event, get_history
import logging
import sqlite3
from datetime import datetime
import random

logger = logging.getLogger(__name__)
router = APIRouter()

def _get_conn():
    """Yield a DB connection; commit and close after the request."""
    conn = create_db()
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

def _get_player_repo(conn: sqlite3.Connection = Depends(_get_conn)):
    return create_player_repository(conn)

def _get_card_repo(conn: sqlite3.Connection = Depends(_get_conn)):
    return create_card_repository(conn)

def _get_review_repo(conn: sqlite3.Connection = Depends(_get_conn)):
    return create_review_repository(conn)

def _get_event_repo(conn: sqlite3.Connection = Depends(_get_conn)):
    return create_event_repository(conn)

@router.post("/trigger")
def trigger(player_repo = Depends(_get_player_repo), 
            card_repo = Depends(_get_card_repo), 
            review_repo = Depends(_get_review_repo),
            event_repo = Depends(_get_event_repo)) -> dict:
    logger.info("POST /api/events/trigger")
    log = trigger_event(player_repo, card_repo, review_repo, event_repo, datetime.now(), random.Random())
    return {
        "success": True, 
        "data": log.model_dump() if log else None,
        "error": None
    }

@router.get("/history")
def history(event_repo = Depends(_get_event_repo), limit: int = 20) -> dict:
    logs = get_history(event_repo, limit)
    return {
        "success": True, 
        "data": [log.model_dump() for log in logs],
        "error": None
    }
