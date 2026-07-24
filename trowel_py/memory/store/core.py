"""Core 与 dictionary 根索引的只读访问。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.types import CoreItem

from .codec import _core_item_from_dict, _split_frontmatter

_CORE_FILE = "core.md"
_DICT_L0 = "dictionary-L0.md"


class _CoreStore:
    root: Path

    def load_core(self) -> str:

        path = self.root / _CORE_FILE
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_dictionary_L0(self) -> str:

        path = self.root / _DICT_L0
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_core_items(self) -> tuple[CoreItem, ...]:

        path = self.root / _CORE_FILE
        if not path.exists():
            return ()
        fm, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
        if not fm:
            return ()
        items = fm.get("items")
        if not isinstance(items, list):
            return ()
        return tuple(
            core_item
            for it in items
            if (core_item := _core_item_from_dict(it)) is not None
        )
