from __future__ import annotations

from enum import Enum


class CandidateStatus(str, Enum):
    NEW = "new"
    SHOWN = "shown"
    ADOPTED = "adopted"
    DISMISSED = "dismissed"
    INVALID = "invalid"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in {
            CandidateStatus.ADOPTED,
            CandidateStatus.DISMISSED,
            CandidateStatus.INVALID,
            CandidateStatus.EXPIRED,
        }
