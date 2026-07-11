"""dual-track audit (slice-040 T8).

After the agent splits the draft into notes (knowledge) + diary (events),
Python audits the diary entries for knowledge signal words that leaked into
the experience track. This is a safety net — the agent does the primary split
(grill §8); Python only flags suspected leaks. It does NOT auto-migrate
(migration needs judgment; the report is for the persist layer / human review).
"""
from __future__ import annotations

from dataclasses import dataclass

from trowel_py.memory.draft import Draft
from trowel_py.memory.prompt import DUALTRACK_SIGNAL_WORDS

#: how many context chars to show around a matched signal word.
_SNIPPET_RADIUS = 15


@dataclass(frozen=True)
class DiaryLeak:
    """One diary entry suspected of holding knowledge-track content.

    Attributes:
        date: the diary entry's date.
        signal: the signal word that matched.
        snippet: surrounding text for a human to judge.
    """

    date: str
    signal: str
    snippet: str


@dataclass(frozen=True)
class DualtrackReport:
    """Result of auditing a draft for cross-track leaks."""

    leaks: tuple[DiaryLeak, ...] = ()

    @property
    def clean(self) -> bool:
        """True when no diary entry tripped a knowledge signal word."""
        return not self.leaks


def audit_draft(draft: Draft) -> DualtrackReport:
    """Scan diary entries for knowledge signal words (C-1 backstop).

    Notes are NOT scanned — they are the knowledge track, so signal words there
    are expected and correct. Only diary entries (the experience track) are
    audited.
    """
    leaks: list[DiaryLeak] = []
    for d in draft.diary:
        text = d.events
        for sig in DUALTRACK_SIGNAL_WORDS:
            idx = text.find(sig)
            if idx != -1:
                start = max(0, idx - _SNIPPET_RADIUS)
                end = min(len(text), idx + len(sig) + _SNIPPET_RADIUS)
                leaks.append(
                    DiaryLeak(date=d.date, signal=sig, snippet=text[start:end])
                )
                break  # one signal per entry is enough to flag it
    return DualtrackReport(leaks=tuple(leaks))
