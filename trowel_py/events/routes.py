"""提供事件触发和历史记录查询的 HTTP 接口。"""

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends
from trowel_py.db.connection import create_db
from trowel_py.cards.repository import create_card_repository
from trowel_py.player.repository import create_player_repository
from trowel_py.review.repository import create_review_repository
from trowel_py.events.repository import create_event_repository
from trowel_py.events.repository import EventRepository
from trowel_py.events.service import trigger_event, get_history
from trowel_py.cards.repository import CardRepository
from trowel_py.player.repository import PlayerRepository
from trowel_py.review.repository import ReviewRepository
import logging
import sqlite3
from datetime import datetime
import random

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_conn() -> Iterator[sqlite3.Connection]:
    """创建当前请求的数据库连接，结束时提交并关闭；异常路径也不会回滚。"""
    conn = create_db()
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def _get_player_repo(
    conn: sqlite3.Connection = Depends(_get_conn),
) -> PlayerRepository:
    """为当前请求创建玩家数据读写对象。"""

    return create_player_repository(conn)


def _get_card_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> CardRepository:
    """为当前请求创建卡片数据读写对象。"""

    return create_card_repository(conn)


def _get_review_repo(
    conn: sqlite3.Connection = Depends(_get_conn),
) -> ReviewRepository:
    """为当前请求创建复习数据读写对象。"""

    return create_review_repository(conn)


def _get_event_repo(
    conn: sqlite3.Connection = Depends(_get_conn),
) -> EventRepository:
    """为当前请求创建事件数据读写对象。"""

    return create_event_repository(conn)


@router.post("/trigger")
def trigger(
    player_repo: PlayerRepository = Depends(_get_player_repo),
    card_repo: CardRepository = Depends(_get_card_repo),
    review_repo: ReviewRepository = Depends(_get_review_repo),
    event_repo: EventRepository = Depends(_get_event_repo),
) -> dict[str, Any]:
    """尝试触发一次可执行事件并返回产生的日志。"""

    logger.info("POST /api/events/trigger")
    log = trigger_event(
        player_repo, card_repo, review_repo, event_repo, datetime.now(), random.Random()
    )
    return {"success": True, "data": log.model_dump() if log else None, "error": None}


@router.get("/history")
def history(
    event_repo: EventRepository = Depends(_get_event_repo), limit: int = 20
) -> dict[str, Any]:
    """按时间从新到旧返回最近的事件日志。"""

    logs = get_history(event_repo, limit)
    return {"success": True, "data": [log.model_dump() for log in logs], "error": None}
