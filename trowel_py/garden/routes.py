from fastapi import APIRouter, Depends
from trowel_py.db.connection import create_db
from trowel_py.garden.repository import GardenRepository, create_garden_repository
from trowel_py.garden.service import get_plants, get_stats
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

def _get_garden_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> GardenRepository:
    return create_garden_repository(conn)

@router.get("/plants")
def plants(garden_repo: GardenRepository = Depends(_get_garden_repo)):
    """
    return all plants with their card data and computed plant stage.
    """
    logger.info("get /api/garden/plants")
    plant_list = get_plants(garden_repo)
    return {
        "success": True, 
        "data": plant_list,
        "error": None
    }

@router.get("/stats")
def stats(garden_repo: GardenRepository = Depends(_get_garden_repo)):
    """
    return aggregated garden statistics
    """
    logger.info("get /api/garden/stats")
    stats = get_stats(garden_repo)
    return {
        "success": True, 
        "data": stats,
        "error": None
    }
