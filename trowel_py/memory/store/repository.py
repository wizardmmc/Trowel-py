"""MemoryStore 的统一门面。"""

from __future__ import annotations

from pathlib import Path

from .core import _CoreStore
from .episodes import _EpisodeStore
from .notes import _NotesStore
from .profile_io import _ProfileStore


class MemoryStore(_NotesStore, _EpisodeStore, _CoreStore, _ProfileStore):
    """读写以 root 为根的 file-backed memory tree。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
