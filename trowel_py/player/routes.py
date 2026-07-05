from fastapi import APIRouter, Depends
from trowel_py.player.service import get_profile, get_inventory, spend_coins
from trowel_py.db.connection import create_db
from trowel_py.player.repository import PlayerRepository, create_player_repository
from trowel_py.schemas.player import BuyItemRequest
import logging
import sqlite3

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


def _get_player_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> PlayerRepository:
    return create_player_repository(conn)


@router.get("")
@router.get("/")
def get_player(player_repo: PlayerRepository = Depends(_get_player_repo)) -> dict:
    """
    return the default player's profile with the computed level fields.
    """
    logger.info("get /api/player")
    profile = get_profile(player_repo)
    return {
        "success": True,
        "data": profile.model_dump(),
        "error": None
    }


@router.get("/inventory")
def inventory(player_repo: PlayerRepository = Depends(_get_player_repo)) -> dict:
    """
    return every item in the default player's inventory.
    """
    logger.info("get /api/player/inventory")
    items = get_inventory(player_repo)
    return {
        "success": True,
        "data": [item.model_dump() for item in items],
        "error": None
    }


@router.post("/buy")
def buy(request: BuyItemRequest,
        player_repo: PlayerRepository = Depends(_get_player_repo)) -> dict:
    """
    buy an item: spend coins and grant it to the inventory.
    """
    logger.info("buy item: %s", request.item_id)
    try:
        item_type = spend_coins(request.item_id, player_repo)
    except ValueError as e:
        # user error (unknown item or not enough coins): not a server fault
        logger.warning("buy failed for %s: %s", request.item_id, e)
        return {
            "success": False,
            "data": None,
            "error": str(e)
        }
    return {
        "success": True,
        "data": {
            "item_id": request.item_id,
            "item_type": item_type
        },
        "error": None
    }
