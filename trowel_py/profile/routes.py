"""提供用户画像读写和画像建议审核的 HTTP 路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from trowel_py.profile.models import Profile, Suggestion
from trowel_py.profile.repository import ProfileRepository
from trowel_py.profile.suggestions import (
    pending_suggestions,
    update_suggestion_status,
)
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
    """把用户画像转换为 HTTP 响应模型。"""
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
    """把一条画像建议转换为 HTTP 响应模型。"""
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
def get_profile(store: ProfileRepository = Depends(get_profile_store)) -> dict:
    """读取当前用户画像；没有可用画像时，五个维度和更新时间为空，来源为 ``user-edit``。"""
    logger.info("get /api/profile")
    profile = store.load_profile()
    return {"success": True, "data": _to_dto(profile).model_dump(), "error": None}


@router.put("")
@router.put("/")
def put_profile(
    update: ProfileUpdate,
    store: ProfileRepository = Depends(get_profile_store),
) -> dict:
    """用请求内容完整替换五个画像维度；未传入的维度按空字符串写入。"""
    logger.info("put /api/profile (source=%s)", update.source)
    try:
        fresh = write_profile(store, update)
    except ValueError as e:
        logger.warning("put /api/profile failed: %s", e)
        return {"success": False, "data": None, "error": str(e)}
    return {"success": True, "data": _to_dto(fresh).model_dump(), "error": None}


@router.get("/suggestions")
def get_suggestions(store: ProfileRepository = Depends(get_profile_store)) -> dict:
    """返回当前画像提炼策略生成且仍待用户审核的建议。"""
    logger.info("get /api/profile/suggestions")
    try:
        items = pending_suggestions(store.root)
    except ValueError as e:
        # 队列内容触发 ValueError 时返回固定文案，不把异常细节带入响应。
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
    store: ProfileRepository = Depends(get_profile_store),
) -> dict:
    """把指定 ID 的全部建议标记为已接受或已丢弃，不写入画像正文。"""
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
