"""Diary 的存储与筛选。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trowel_py.memory.schema import validate_entry
from trowel_py.memory.types import Diary

from .codec import _diary_from_fm, _dump_frontmatter, _split_frontmatter

_DIARY_DIR = "diary"
_LAYER_DIR = {"day": "daily", "week": "weekly", "month": "monthly"}


class _DiaryStore:
    root: Path

    def write_diary(self, entry: dict[str, Any]) -> str:

        fm = {k: v for k, v in entry.items() if not k.startswith("__")}
        fm["type"] = "diary"
        result = validate_entry("diary", fm)
        if not result.ok:
            raise ValueError(f"invalid diary: {result.errors}")
        layer = str(fm.get("layer", "day"))
        date = str(fm.get("date", ""))
        dir_name = _LAYER_DIR.get(layer, "daily")
        path = self.root / _DIARY_DIR / dir_name / f"{date}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _dump_frontmatter(fm, entry.get("__body", "")), encoding="utf-8"
        )
        return date

    def load_diary(
        self, since: str | None = None, layer: str | None = None
    ) -> list[Diary]:
        """since 对 ISO 日期字符串执行包含下界过滤。"""

        diary_root = self.root / _DIARY_DIR
        if not diary_root.exists():
            return []
        out: list[Diary] = []
        for p in sorted(diary_root.rglob("*.md")):
            fm, body = _split_frontmatter(p.read_text(encoding="utf-8"))
            d = _diary_from_fm(fm, body)
            if d is None:
                continue
            if layer and d.layer != layer:
                continue
            if since and d.date < since:
                continue
            out.append(d)
        return out
