"""dictionary consistency state (slice-064 §5: observability).

The dictionary is a *derived* index over ``notes/``. Its state file records
whether the on-disk L0/L1 currently matches the note facts, plus the last
success/failure provenance. Mirrors ``tidy_state`` (slice-063): lives at
``<root>/meta/dictionary-state.json``, written atomically (temp + os.replace),
and a missing/corrupt file is NEVER read as "consistent" — it bootstraps to
``missing`` so the next check rebuilds instead of trusting a stale index
(C-7). The success watermark (``source_hash`` / ``last_success_at``) survives a
later failure so the operator can see what the index last agreed with.

``status`` is the four-state contract from slice-064 §2/§5:
    consistent — index agrees with the current active notes;
    stale      — index exists but is known to disagree (or a rebuild failed);
    missing    — no index on disk (cold start, or wiped after corrupt state).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_STATE_REL = "meta/dictionary-state.json"
DictStatus = Literal["consistent", "stale", "missing"]


@dataclass(frozen=True)
class DictionaryState:
    """Snapshot of dictionary agreement with the note facts.

    Attributes:
        status: consistent | stale | missing.
        source_hash: the source_hash of the active-note corpus the index was
            last successfully built from. Preserved across a later failure so
            the operator can see the last-good hash. None until first success.
        last_success_at: ISO timestamp of the last successful build/check.
        last_failure_at: ISO timestamp of the last failed build/sync.
        last_failure_reason: short reason for the last failure.
    """

    status: DictStatus = "missing"
    source_hash: str | None = None
    rendered_hash: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "source_hash": self.source_hash,
            "rendered_hash": self.rendered_hash,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_failure_reason": self.last_failure_reason,
        }

    @classmethod
    def from_dict(cls, d: object) -> "DictionaryState":
        if not isinstance(d, dict):
            return cls()
        status = d.get("status")
        if status not in ("consistent", "stale", "missing"):
            status = "missing"
        return cls(
            status=status,  # type: ignore[arg-type]
            source_hash=_opt_str(d.get("source_hash")),
            rendered_hash=_opt_str(d.get("rendered_hash")),
            last_success_at=_opt_str(d.get("last_success_at")),
            last_failure_at=_opt_str(d.get("last_failure_at")),
            last_failure_reason=_opt_str(d.get("last_failure_reason")),
        )

    def with_success(
        self, source_hash: str, rendered_hash: str, at: str
    ) -> "DictionaryState":
        """Immutable copy: index just agreed with ``source_hash`` (the note
        facts) and ``rendered_hash`` (the on-disk L0+L1 content) at ``at``."""
        return replace(
            self,
            status="consistent",
            source_hash=source_hash,
            rendered_hash=rendered_hash,
            last_success_at=at,
            last_failure_at=None,
            last_failure_reason=None,
        )

    def with_failure(self, reason: str, at: str) -> "DictionaryState":
        """Immutable copy: a rebuild/sync failed; keep the last-good hash (C-7)."""
        return replace(
            self,
            status="stale",
            last_failure_at=at,
            last_failure_reason=reason,
        )


def _opt_str(v: object) -> str | None:
    if v is None:
        return None
    return str(v)


def state_path(root: Path | str) -> Path:
    """Absolute path of the state file under ``root``."""
    return Path(root) / _STATE_REL


def load_state(root: Path | str) -> DictionaryState:
    """Load the state. Missing/corrupt → ``DictionaryState()`` (missing).

    A corrupt file is never read as "consistent": we log and return the empty
    state so the next check treats the index as untrusted (C-7).
    """
    path = state_path(root)
    if not path.exists():
        return DictionaryState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[memory] dictionary state corrupt (%s) — treating as missing", exc
        )
        return DictionaryState()
    return DictionaryState.from_dict(data)


def save_state(root: Path | str, state: DictionaryState) -> None:
    """Atomically replace the state file (temp + ``os.replace``, C-6).

    State is written LAST — the L0/L1 are already on disk by the time we stamp
    ``consistent``, so a crash here at most causes a safe re-check on the next
    run (never a false "consistent" after a half-written index).
    """
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
