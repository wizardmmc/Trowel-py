"""profile HTTP schemas (slice-049): pydantic DTOs for /api/profile.

``ProfileUpdate`` is the PUT body (five dims only — ``updated``/``source`` are
server-stamped, never accepted from the client). ``ProfileDTO`` is the
GET/PUT response (five dims + provenance). Both mirror the five-dim Profile
dataclass (slice-047); the ``other`` escape hatch is always present, never
dropped back to four.
"""
from __future__ import annotations

from pydantic import BaseModel


class ProfileUpdate(BaseModel):
    """PUT /api/profile body: the five editable dimensions.

    All default to ``""`` so a partial PUT (e.g. cold-start seeding one dim)
    is valid. ``updated``/``source`` are NOT accepted here — the server stamps
    them (today's ISO date, ``user-edit``).
    """

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""


class ProfileDTO(BaseModel):
    """GET/PUT /api/profile response: five dims + provenance."""

    ability: str
    methodology: str
    expression: str
    goal: str
    other: str
    updated: str
    source: str
