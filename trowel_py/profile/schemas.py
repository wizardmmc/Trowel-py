"""profile HTTP schemas (slice-049 + slice-050): pydantic DTOs for /api/profile.

``ProfileUpdate`` is the PUT body. slice-050 adds an optional ``source``
(default ``user-edit``): the front-end passes ``ai-calibration`` when the PUT
merges accepted AI suggestions, so the file-level provenance stamp records the
nature of the last commit (047 grill: source is file-level, not per-field).
``ProfileDTO`` is the GET/PUT response. slice-050 adds ``SuggestionDTO`` (the
candidate-queue item) + ``SuggestionStatusUpdate`` (accept / discard).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from trowel_py.memory.types import ProfileDimension, SuggestionStatus


class ProfileUpdate(BaseModel):
    """PUT /api/profile body: the five editable dimensions + optional source.

    All dims default to ``""`` so a partial PUT (e.g. cold-start seeding one
    dim) is valid. ``updated`` is always server-stamped (today's ISO date).
    ``source`` defaults to ``user-edit`` (a hand edit); the front-end passes
    ``ai-calibration`` when merging accepted AI suggestions (slice-050).
    """

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""
    source: Literal["user-edit", "ai-calibration"] = "user-edit"


class ProfileDTO(BaseModel):
    """GET/PUT /api/profile response: five dims + provenance."""

    ability: str
    methodology: str
    expression: str
    goal: str
    other: str
    updated: str
    source: str


class SuggestionDTO(BaseModel):
    """one AI-proposed profile addition (slice-050 candidate-queue item)."""

    id: str
    dimension: ProfileDimension
    body: str
    sources: list[str]
    date: str
    status: SuggestionStatus


class SuggestionStatusUpdate(BaseModel):
    """PATCH /api/profile/suggestions/{id} body: accept / discard."""

    status: Literal["accepted", "discarded"]
