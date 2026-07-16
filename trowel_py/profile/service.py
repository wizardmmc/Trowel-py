"""profile HTTP service (slice-049): thin adapter over the memory store.

The store (slice-047) owns ``profile.md`` IO + snapshot insurance; this module
only adapts it for FastAPI: a dependency that resolves the store at the
memory root, plus a write helper that stamps provenance and re-reads to
confirm. Domain logic (body serialize/parse/validate) stays in
``trowel_py.memory.profile`` — this layer never duplicates it.
"""
from __future__ import annotations

from datetime import date

from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile
from trowel_py.profile.schemas import ProfileUpdate


def get_profile_store() -> MemoryStore:
    """FastAPI dependency: the profile store at the resolved memory root.

    Override in tests via ``app.dependency_overrides[get_profile_store]`` to
    point at a ``tmp_path`` root (mirrors player's ``_get_conn`` override, and
    avoids touching the real ``~/.trowel/memory``).
    """
    return MemoryStore(resolve_memory_root())


def write_profile(store: MemoryStore, update: ProfileUpdate) -> Profile:
    """Stamp provenance, write via the store, and re-read to confirm.

    ``updated`` is today (ISO date). ``source`` comes from the update body
    (default ``user-edit``; the front-end passes ``ai-calibration`` when
    merging accepted AI suggestions — slice-050). Returns the freshly loaded
    profile so the HTTP response reflects exactly what landed on disk.

    Raises:
        ValueError: the profile or source tag fails store validation.
    """
    profile = Profile(
        ability=update.ability,
        methodology=update.methodology,
        expression=update.expression,
        goal=update.goal,
        other=update.other,
        updated=date.today().isoformat(),
    )
    store.write_profile(profile, source=update.source)
    return store.load_profile()
