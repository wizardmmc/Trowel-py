"""profile distill watermark state (slice-050).

Tracks which CC sessions the distill job has already distilled-for-profile.
INDEPENDENT from review_job's memory watermark (C-7: never touches
sessions.db) so the two daily jobs don't trip over each other — review
advancing its ``last_extracted_offset`` must not hide a session from distill,
and vice versa.

Stored as ``meta/profile-distill-state.json`` (a ``processed`` list, deduped by
cc_session_id on load). Bare ``write_text`` like the rest of the store; the
distill job holds a flock so concurrent marks can't interleave.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_STATE_FILE = "profile-distill-state.json"


@dataclass(frozen=True)
class ProcessedSession:
    """One session the distill job has finished processing.

    Attributes:
        cc_session_id: cc's session uuid (the jsonl filename stem).
        end_offset: the completed byte offset up to which the session was
            distilled (so a resumed session's new tail can be re-distilled).
        at: ISO timestamp of when the session was marked processed.
    """

    cc_session_id: str
    end_offset: int
    at: str


def _state_path(root: Path) -> Path:
    """Where the distill watermark lives under a memory root."""
    return root / _META_DIR / _STATE_FILE


def load_processed(root: Path) -> dict[str, ProcessedSession]:
    """Return ``{cc_session_id: ProcessedSession}``. Missing file → ``{}``.

    Later records for the same cc_session_id win (the file is append-then-dedup
    by key on load, so a hand-edited duplicate collapses cleanly).

    Raises:
        ValueError: the file exists but is corrupt JSON (loud, not silent).
    """
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt distill state at {path}: {exc}") from exc
    raw = data.get("processed", []) if isinstance(data, dict) else []
    out: dict[str, ProcessedSession] = {}
    for item in raw:
        if not isinstance(item, dict) or "cc_session_id" not in item:
            continue
        # a corrupt end_offset (null / non-numeric) must NOT crash the whole
        # load → sticky batch failure (code-review [2]). Skip the bad row +
        # warn so that session just gets re-distilled instead of blocking all.
        try:
            end_offset = int(item.get("end_offset", 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            logger.warning(
                "distill state: corrupt end_offset %r for %s, skipping",
                item.get("end_offset"),
                item.get("cc_session_id"),
            )
            continue
        rec = ProcessedSession(
            cc_session_id=str(item["cc_session_id"]),
            end_offset=end_offset,
            at=str(item.get("at", "")),
        )
        out[rec.cc_session_id] = rec
    return out


def mark_processed(
    root: Path, cc_session_id: str, end_offset: int, *, at: str
) -> None:
    """Record that a session was distilled-for-profile (idempotent overwrite).

    Keyed by ``cc_session_id`` so reprocessing a resumed session replaces (not
    duplicates) its record. Read-modify-rewrite the whole file; the caller
    holds a flock so concurrent marks don't interleave.
    """
    existing = load_processed(root)
    existing[cc_session_id] = ProcessedSession(
        cc_session_id=cc_session_id, end_offset=end_offset, at=at
    )
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed": [
            {
                "cc_session_id": r.cc_session_id,
                "end_offset": r.end_offset,
                "at": r.at,
            }
            for r in existing.values()
        ]
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
