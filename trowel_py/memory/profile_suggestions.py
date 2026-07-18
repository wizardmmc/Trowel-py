"""profile suggestion candidate-queue IO (slice-050).

The distill job appends AI-derived profile suggestions here as ``pending``
candidates; the front-end lists them, the user accepts (→ merged into
profile.md via ``PUT /api/profile``) or discards. The agent NEVER gets the
profile write path (C-1 structural provenance) — this queue is the proposal
side-channel.

Stored as ``meta/profile-suggestions.json`` (a single JSON object with a
``suggestions`` array + an ``updated`` stamp), sibling to ``sessions.db`` and
the review lock under ``meta/``. Bare ``write_text`` like the rest of the
store — no atomic write; the caller (distill job) holds a flock during append
so concurrent appends can't interleave.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Sequence, cast

try:  # Unix-only; Windows has no flock → the lock becomes a no-op there.
    import fcntl
except ImportError:  # pragma: no cover — non-Unix
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.types import ProfileDimension, Suggestion, SuggestionStatus

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_SUGGESTIONS_FILE = "profile-suggestions.json"

#: slice-067: the current profile-distill policy version. v1 = the open-ended
#: slice-050 prompt (long bodies, over-attribution); v2 = the ten hard rules +
#: Python structure gate. New suggestions stamp this; the default-aged GET
#: pending API surfaces only this policy's pending items. Bump here when the
#: distill rules change again — old records stay on disk as their own version
#: (C-6 版本可审计: never rewrite v1 in place).
PROFILE_DISTILL_POLICY_VERSION = 2

#: the closed sets backing the Literal enums in types.py. Kept here (not
#: derived from typing.get_args) so a corrupt on-disk value is rejected with a
#: clear message instead of a typing-layer surprise.
_VALID_DIMS: frozenset[str] = frozenset(
    {"ability", "methodology", "expression", "goal", "other"}
)
_VALID_STATUSES: frozenset[str] = frozenset({"pending", "accepted", "discarded"})


@contextlib.contextmanager
def _suggestions_lock(root: Path):
    """Exclusive flock over the queue's read-modify-write (slice-050 CR fix).

    The daily distill job (under its own ``_distill_lock``) and the HTTP PATCH
    path both do load → modify → write on the queue file; without this lock
    their loads could interleave and one write would clobber the other's
    change. Blocking (``LOCK_EX``, no NB): callers run in worker threads / sync
    handlers, so waiting is fine. Off-Unix (``fcntl`` is None) it's a no-op.
    """
    if fcntl is None:
        yield
        return
    lock_path = root / _META_DIR / ".suggestions.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _queue_path(root: Path) -> Path:
    """Where the suggestion queue lives under a memory root."""
    return root / _META_DIR / _SUGGESTIONS_FILE


def _suggestion_from_dict(item: dict[str, object]) -> Suggestion:
    """Parse one suggestion dict, validating dimension + status are known.

    Raises:
        ValueError: dimension or status is outside the closed enum, or the
            shape is wrong (keeps the lifecycle enumerable + auditable on disk).
    """
    dim = item.get("dimension")
    status = item.get("status", "pending")
    if dim not in _VALID_DIMS:
        raise ValueError(f"unknown dimension {dim!r} in suggestion queue")
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown status {status!r} in suggestion queue")
    if not item.get("id"):
        raise ValueError("suggestion missing id in queue")
    sources = item.get("sources", [])
    # slice-067: policy_version compat. v1 records on disk predate the field;
    # read them as 1 rather than failing or batch-rewriting the queue (C-6).
    raw_pv = item.get("policy_version", 1)
    if isinstance(raw_pv, bool):  # bool is an int subclass — never a real version
        policy_version = 1
    elif isinstance(raw_pv, int):
        policy_version = raw_pv
    else:
        try:
            policy_version = int(str(raw_pv))
        except (TypeError, ValueError):
            policy_version = 1
    return Suggestion(
        id=str(item["id"]),
        dimension=cast(ProfileDimension, dim),
        body=str(item.get("body") or ""),
        sources=tuple(str(s) for s in sources) if isinstance(sources, list) else (),
        date=str(item.get("date", "")),
        status=cast(SuggestionStatus, status),
        policy_version=policy_version,
    )


def suggestion_to_dict(s: Suggestion) -> dict[str, object]:
    """Serialize one suggestion to its on-disk dict form (sources → list).

    The single serialization source for ``Suggestion``: the live queue writer
    AND the slice-067 recalibration staging writer both go through here, so a
    new ``Suggestion`` field lands in both shapes together (no drift between
    live queue and staging audit artifact).
    """
    return {
        "id": s.id,
        "dimension": s.dimension,
        "body": s.body,
        "sources": list(s.sources),
        "date": s.date,
        "status": s.status,
        "policy_version": s.policy_version,
    }


def _load_queue(root: Path) -> tuple[list[Suggestion], str]:
    """Load ``(suggestions, updated_stamp)``. Missing file → ``([], "")``.

    Raises:
        ValueError: the file exists but is corrupt (bad JSON, or a suggestion
            carries an unknown dimension/status).
    """
    path = _queue_path(root)
    if not path.exists():
        return [], ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt suggestion queue at {path}: {exc}") from exc
    raw = data.get("suggestions", []) if isinstance(data, dict) else []
    updated = str(data.get("updated", "")) if isinstance(data, dict) else ""
    items = [
        _suggestion_from_dict(item)
        for item in raw
        if isinstance(item, dict)
    ]
    return items, updated


def _write_queue(
    root: Path, items: Sequence[Suggestion], *, updated: str
) -> None:
    """Rewrite the whole queue file (parent dir created if needed)."""
    path = _queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suggestions": [suggestion_to_dict(s) for s in items],
        "updated": updated,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_suggestions(root: Path) -> list[Suggestion]:
    """Return all suggestions in the queue (any status), or ``[]`` if absent.

    Raises:
        ValueError: the file exists but is corrupt (bad JSON, or a suggestion
            has an unknown dimension/status) — corruption must be loud, not
            silently coerced to ``[]`` (C-6 distinguishes "no data" from
            "damaged data").
    """
    items, _updated = _load_queue(root)
    return items


def append_suggestions(
    root: Path, items: Sequence[Suggestion], *, updated: str
) -> None:
    """Append suggestions to the queue and restamp ``updated``.

    No dedup here — the distill agent already deduped against the queue + the
    live profile at generation time (C-8). Read-append-rewrite under
    ``_suggestions_lock`` so a concurrent HTTP PATCH can't interleave and
    clobber this append (slice-050 CR fix).
    """
    with _suggestions_lock(root):
        existing, _old_updated = _load_queue(root)
        _write_queue(root, [*existing, *items], updated=updated)


def update_suggestion_status(
    root: Path, suggestion_id: str, status: SuggestionStatus
) -> None:
    """Flip one suggestion's status (accept / discard); preserve order + stamp.

    Raises:
        KeyError: ``suggestion_id`` is not in the queue.
        ValueError: ``status`` is outside the known lifecycle.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    with _suggestions_lock(root):
        items, updated = _load_queue(root)
        found = False
        new_items: list[Suggestion] = []
        for s in items:
            if s.id == suggestion_id:
                new_items.append(replace(s, status=status))
                found = True
            else:
                new_items.append(s)
        if not found:
            raise KeyError(suggestion_id)
        # a status change is a UI action, not a distill run — keep the queue's
        # `updated` stamp as-is so it still reflects the last distill append.
        _write_queue(root, new_items, updated=updated)


def pending_suggestions(
    root: Path, *, current_policy_version: int = PROFILE_DISTILL_POLICY_VERSION
) -> list[Suggestion]:
    """Return only ``status=pending`` suggestions (what the front-end shows).

    slice-067: by default surface ONLY the current policy's pending items, so
    v1's long/over-attributed bodies stop polluting the review list. v1 pending
    records stay on disk for audit (not deleted, not coerced to discarded —
    C-6 版本可审计). Pass ``current_policy_version=None`` to see every pending
    item regardless of version (audit / recalibration tooling).
    """
    items = [s for s in load_suggestions(root) if s.status == "pending"]
    if current_policy_version is None:
        return items
    return [s for s in items if s.policy_version == current_policy_version]
