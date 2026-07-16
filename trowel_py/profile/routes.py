"""profile HTTP routes (slice-049 GET/PUT + slice-050 suggestion queue).

The envelope ``{success, data, error}`` is inlined as literal dicts (mirrors
player routes — the repo has no shared envelope helper). Reads/writes go
through the store (slice-047), never bypassing it.

slice-050 adds the suggestion candidate-queue endpoints:
- ``GET /api/profile/suggestions``: the pending AI suggestions for the user.
- ``PATCH /api/profile/suggestions/{id}``: accept (→ the front-end then merges
  it into the profile via PUT) or discard. The agent never writes profile.md —
  accept is the user's explicit action, and even then it lands through the
  normal PUT path with ``source=ai-calibration`` (C-1 structural provenance).
"""
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
    """Project the domain Profile into the response DTO."""
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
    """Project a domain Suggestion into the response DTO (sources tuple → list)."""
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
    """return the user self-description profile (empty Profile on cold start)."""
    logger.info("get /api/profile")
    profile = store.load_profile()
    return {"success": True, "data": _to_dto(profile).model_dump(), "error": None}


@router.put("")
@router.put("/")
def put_profile(
    update: ProfileUpdate,
    store: MemoryStore = Depends(get_profile_store),
) -> dict:
    """write the five dims back to profile.md via the store."""
    logger.info("put /api/profile (source=%s)", update.source)
    try:
        fresh = write_profile(store, update)
    except ValueError as e:
        logger.warning("put /api/profile failed: %s", e)
        return {"success": False, "data": None, "error": str(e)}
    return {"success": True, "data": _to_dto(fresh).model_dump(), "error": None}


@router.get("/suggestions")
def get_suggestions(store: MemoryStore = Depends(get_profile_store)) -> dict:
    """return the pending AI profile suggestions for the user to review."""
    logger.info("get /api/profile/suggestions")
    try:
        items = pending_suggestions(store.root)
    except ValueError as e:
        # a corrupt queue file (bad JSON / unknown enum) → report a friendly
        # error instead of leaking the internal path through the 500 handler.
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
    """accept / discard one suggestion by flipping its status.

    Accept does NOT itself write profile.md — the front-end reads the accepted
    suggestion, merges it into the profile, and PUTs with
    ``source=ai-calibration`` (C-1). This endpoint only records the user's
    decision in the queue.
    """
    logger.info(
        "patch /api/profile/suggestions/%s -> %s", suggestion_id, update.status
    )
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
