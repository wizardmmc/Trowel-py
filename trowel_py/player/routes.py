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
    """请求结束时提交并关闭连接，异常路径也不回滚。"""
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
    """返回默认玩家资料及派生等级字段。"""
    logger.info("get /api/player")
    profile = get_profile(player_repo)
    return {"success": True, "data": profile.model_dump(), "error": None}


@router.get("/inventory")
def inventory(player_repo: PlayerRepository = Depends(_get_player_repo)) -> dict:
    """返回默认玩家的全部库存物品。"""
    logger.info("get /api/player/inventory")
    items = get_inventory(player_repo)
    return {
        "success": True,
        "data": [item.model_dump() for item in items],
        "error": None,
    }


@router.post("/buy")
def buy(
    request: BuyItemRequest, player_repo: PlayerRepository = Depends(_get_player_repo)
) -> dict:
    """扣除金币购买商品并加入库存。"""
    logger.info("buy item: %s", request.item_id)
    try:
        item_type = spend_coins(request.item_id, player_repo)
    except ValueError as e:
        # 商品不存在或余额不足沿用成功状态码的错误 envelope。
        logger.warning("buy failed for %s: %s", request.item_id, e)
        return {"success": False, "data": None, "error": str(e)}
    return {
        "success": True,
        "data": {"item_id": request.item_id, "item_type": item_type},
        "error": None,
    }
