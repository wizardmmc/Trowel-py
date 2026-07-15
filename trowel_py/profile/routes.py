"""profile HTTP routes (slice-049): GET/PUT /api/profile.

The envelope ``{success, data, error}`` is inlined as literal dicts (mirrors
player routes — the repo has no shared envelope helper; C-4 "reuse player
pattern" means inline, not a new wrapper). Reads/writes go through the store
(slice-047), never bypassing it; PUT stamps ``updated`` (today) +
``source="user-edit"`` and re-reads to confirm (C-5).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile
from trowel_py.profile.schemas import ProfileDTO, ProfileUpdate
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
    logger.info("put /api/profile")
    try:
        fresh = write_profile(store, update)
    except ValueError as e:
        # Defense-in-depth: on the current HTTP path this is unreachable —
        # write_profile stamps updated (today) + source="user-edit" (both
        # valid) and dims come from pydantic-validated str, so validate_profile
        # never raises here. Kept so a future store change (e.g. source
        # parameterized from the request) still reports a 200 success:False
        # instead of leaking a 500 through the global handler.
        logger.warning("put /api/profile failed: %s", e)
        return {"success": False, "data": None, "error": str(e)}
    return {"success": True, "data": _to_dto(fresh).model_dump(), "error": None}
