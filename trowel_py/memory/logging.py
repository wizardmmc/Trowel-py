"""raw access/outcome logging — the observability insurance substrate (slice-038).

Append-only JSONL of RAW ingredients only. Per C-9 this layer NEVER
pre-classifies (no is_miss / verdict / novel-vs-miss labels): online judgment is
done by the review-reflection (``reflection.py``) over these logs. If a
reflection ever looks wrong, the raw log is the recomputeable insurance.

- access-log: which note bodies were opened during a lookup (also feeds
  ``record_ref`` retirement stats).
- outcome-log: raw session outcome ingredients (retry count, corrections,
  transcript ref) — the negative signals that *might* indicate a recall miss.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_ACCESS_LOG = "access-log.jsonl"
_OUTCOME_LOG = "outcome-log.jsonl"


@dataclass(frozen=True)
class AccessRecord:
    """One note-open event.

    Attributes:
        note_id: the note filename stem that was opened.
        when: ISO timestamp / turn id of the open.
        context_ref: session or turn that triggered the open.
    """

    note_id: str
    when: str
    context_ref: str = ""


@dataclass(frozen=True)
class OutcomeRecord:
    """Raw session outcome ingredients (NO classification — C-9).

    Attributes:
        session_ref: the session this outcome belongs to.
        when: ISO timestamp.
        retry_count: how many retries happened (a possible miss signal).
        corrections: how many times the human corrected the model.
        transcript_ref: where the full transcript lives for recompute.
    """

    session_ref: str
    when: str
    retry_count: int = 0
    corrections: int = 0
    transcript_ref: str = ""


def log_note_access(root: Path | str, note_id: str, when: str,
                    context_ref: str = "") -> None:
    """Append a note-open record to access-log.jsonl."""
    _append(Path(root) / _META_DIR / _ACCESS_LOG,
            asdict(AccessRecord(note_id=note_id, when=when, context_ref=context_ref)))


def log_session_outcome(root: Path | str, session_ref: str, when: str, *,
                        retry_count: int = 0, corrections: int = 0,
                        transcript_ref: str = "") -> None:
    """Append a raw session-outcome record to outcome-log.jsonl (no label, C-9)."""
    _append(Path(root) / _META_DIR / _OUTCOME_LOG,
            asdict(OutcomeRecord(session_ref=session_ref, when=when,
                                 retry_count=retry_count, corrections=corrections,
                                 transcript_ref=transcript_ref)))


def read_access_log(root: Path | str) -> list[AccessRecord]:
    """Return all access records in append order (empty list if absent)."""
    return _read(Path(root) / _META_DIR / _ACCESS_LOG, AccessRecord)


def read_outcome_log(root: Path | str) -> list[OutcomeRecord]:
    """Return all outcome records in append order (empty list if absent)."""
    return _read(Path(root) / _META_DIR / _OUTCOME_LOG, OutcomeRecord)


def _append(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read(path: Path, cls: type) -> list[Any]:
    """Read all records in append order, skipping corrupt lines (W1).

    The logs are append-only insurance written by concurrent writers (review
    jobs, retries); one bad line must not make the whole history unreadable —
    that would defeat the "recomputeable insurance" role. Bad lines are skipped
    with a warning (file + line number + head of the line).
    """
    if not path.exists():
        return []
    out: list[Any] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping corrupt %s line %d: %r", path.name, i, line[:80])
            continue
        try:
            out.append(cls(**obj))
        except TypeError:
            logger.warning("skipping malformed %s line %d (missing keys)", path.name, i)
    return out
