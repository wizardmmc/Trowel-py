"""profile 与建议队列的 HTTP 路由，所有读写都经过 memory store。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from trowel_py.memory.profile_suggestions import (
    pending_suggestions,
    update_suggestion_status,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile, Suggestion
from trowel_py.profile.schemas import (
    ProfileDTO,
    ProfileUpdate,
    SuggestionDTO,
    SuggestionStatusUpdate,
)
from trowel_py.profile.service import get_profile_store, write_profile

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_dto(p: Profile) -> ProfileDTO:
    return ProfileDTO(
        ability=p.ability,
        methodology=p.methodology,
        expression=p.expression,
        goal=p.goal,
        other=p.other,
        updated=p.updated,
        source=p.source,
    )


def _to_suggestion_dto(s: Suggestion) -> SuggestionDTO:
    return SuggestionDTO(
        id=s.id,
        dimension=s.dimension,
        body=s.body,
        sources=list(s.sources),
        date=s.date,
        status=s.status,
    )


@router.get("")
@router.get("/")
def get_profile(store: MemoryStore = Depends(get_profile_store)) -> dict:
    """返回用户画像；首次使用时返回空画像。"""
    logger.info("get /api/profile")
    profile = store.load_profile()
    return {"success": True, "data": _to_dto(profile).model_dump(), "error": None}


@router.put("")
@router.put("/")
def put_profile(
    update: ProfileUpdate,
    store: MemoryStore = Depends(get_profile_store),
) -> dict:
    """通过 store 写入五个画像维度。"""
    logger.info("put /api/profile (source=%s)", update.source)
    try:
        fresh = write_profile(store, update)
    except ValueError as e:
        logger.warning("put /api/profile failed: %s", e)
        return {"success": False, "data": None, "error": str(e)}
    return {"success": True, "data": _to_dto(fresh).model_dump(), "error": None}


@router.get("/suggestions")
def get_suggestions(store: MemoryStore = Depends(get_profile_store)) -> dict:
    """返回等待用户审核的画像建议。"""
    logger.info("get /api/profile/suggestions")
    try:
        items = pending_suggestions(store.root)
    except ValueError as e:
        # 队列损坏时返回稳定错误，不能通过全局 500 响应泄露内部路径。
        logger.warning("get /api/profile/suggestions: corrupt queue: %s", e)
        return {"success": False, "data": None, "error": "建议队列读取失败"}
    return {
        "success": True,
        "data": [_to_suggestion_dto(s).model_dump() for s in items],
        "error": None,
    }


@router.patch("/suggestions/{suggestion_id}")
def patch_suggestion(
    suggestion_id: str,
    update: SuggestionStatusUpdate,
    store: MemoryStore = Depends(get_profile_store),
) -> dict:
    """记录用户接受或丢弃建议的决定，但不直接写入 profile.md。"""
    logger.info("patch /api/profile/suggestions/%s -> %s", suggestion_id, update.status)
    try:
        update_suggestion_status(store.root, suggestion_id, update.status)
    except KeyError:
        return {
            "success": False,
            "data": None,
            "error": f"suggestion {suggestion_id} not found",
        }
    except ValueError as e:
        return {"success": False, "data": None, "error": str(e)}
    return {"success": True, "data": None, "error": None}
