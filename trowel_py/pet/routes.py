"""提供宠物状态、喂食、互动和装备接口。"""

import logging
import random
import sqlite3
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends

from trowel_py.db.connection import create_db
from trowel_py.pet.brain import PetBrain, TemplateBrain
from trowel_py.pet.repository import PetRepository, create_pet_repository
from trowel_py.player.repository import PlayerRepository, create_player_repository
from trowel_py.pet.service import get_pet, feed, interact, equip_hat
from trowel_py.pet.schemas import FeedRequest, EquipRequest

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


def _get_pet_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> PetRepository:
    """为请求创建宠物数据仓库。"""
    return create_pet_repository(conn)


def _get_player_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> PlayerRepository:
    """为请求创建玩家数据仓库。"""
    return create_player_repository(conn)


def _get_brain() -> PetBrain:
    """为请求创建固定文本宠物回应器。"""
    return TemplateBrain()


@router.get("")
@router.get("/")
def get_pet_route(
    pet_repo: PetRepository = Depends(_get_pet_repo),
) -> dict[str, Any]:
    """返回宠物当前状态。"""
    logger.info("GET /api/pet")
    pet = get_pet(pet_repo)
    return {"success": True, "data": pet.model_dump(), "error": None}


@router.post("/feed")
def feed_route(
    request: FeedRequest,
    pet_repo: PetRepository = Depends(_get_pet_repo),
    player_repo: PlayerRepository = Depends(_get_player_repo),
) -> dict[str, Any]:
    """消耗一件库存食物喂养宠物。"""
    logger.info("POST /api/pet/feed item=%s", request.item_id)
    try:
        pet = feed(request.item_id, pet_repo, player_repo)
    except ValueError as e:
        # 商品不存在、类型错误等输入问题沿用成功状态码的错误 envelope。
        logger.warning("feed failed: %s", e)
        return {"success": False, "data": None, "error": str(e)}
    return {"success": True, "data": pet.model_dump(), "error": None}


@router.post("/interact")
def interact_route(
    pet_repo: PetRepository = Depends(_get_pet_repo),
    brain: PetBrain = Depends(_get_brain),
) -> dict[str, Any]:
    """与宠物互动，使其心情变好并返回一句回应。"""
    logger.info("POST /api/pet/interact")
    result = interact(pet_repo, brain, random.Random())
    response = result["response"]
    pet = result["pet"]
    return {
        "success": True,
        "data": {
            "response": {"text": response.text, "mood": response.mood},
            "pet": pet.model_dump(),
        },
        "error": None,
    }


@router.put("/equip")
def equip_route(
    request: EquipRequest,
    pet_repo: PetRepository = Depends(_get_pet_repo),
    player_repo: PlayerRepository = Depends(_get_player_repo),
) -> dict[str, Any]:
    """为宠物装备一件库存帽子。"""
    logger.info("PUT /api/pet/equip item=%s", request.item_id)
    try:
        pet = equip_hat(request.item_id, pet_repo, player_repo)
    except ValueError as e:
        logger.warning("equip failed: %s", e)
        return {"success": False, "data": None, "error": str(e)}
    return {"success": True, "data": pet.model_dump(), "error": None}
