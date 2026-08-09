"""提供花园植物列表和聚合统计接口。"""

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends
from trowel_py.db.connection import create_db
from trowel_py.garden.repository import GardenRepository, create_garden_repository
from trowel_py.garden.service import get_plants, get_stats
import logging
import sqlite3

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_conn() -> Iterator[sqlite3.Connection]:
    """为一次请求提供数据库连接。

    请求结束时提交并关闭连接，异常路径也不回滚。
    """
    conn = create_db()
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def _get_garden_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> GardenRepository:
    """为请求创建花园数据仓库。"""
    return create_garden_repository(conn)


@router.get("/plants", response_model=None)
def plants(
    garden_repo: GardenRepository = Depends(_get_garden_repo),
) -> dict[str, Any]:
    """返回所有植物及其卡片数据和派生生长阶段。"""
    logger.info("get /api/garden/plants")
    plant_list = get_plants(garden_repo)
    return {"success": True, "data": plant_list, "error": None}


@router.get("/stats", response_model=None)
def stats(
    garden_repo: GardenRepository = Depends(_get_garden_repo),
) -> dict[str, Any]:
    """返回花园聚合统计。"""
    logger.info("get /api/garden/stats")
    stats = get_stats(garden_repo)
    return {"success": True, "data": stats, "error": None}
