"""Memory injection 测试数据。"""

from __future__ import annotations

from pathlib import Path

import yaml

from trowel_py.memory.store import MemoryStore


def item(item_id: str, imperative: str, status: str = "active") -> dict:
    return {
        "id": item_id,
        "imperative": imperative,
        "scope": "high-risk",
        "status": status,
        "source": "test",
    }


def write_core(root: Path, items: list[dict]) -> None:
    frontmatter = {"type": "core", "items": items}
    dumped = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
    (root / "core.md").write_text(
        f"---\n{dumped}---\nbody\n",
        encoding="utf-8",
    )


def write_l0(root: Path, text: str) -> None:
    (root / "dictionary-L0.md").write_text(text, encoding="utf-8")


def write_diary(root: Path, date_value: str, layer: str, body: str) -> None:
    subdirectory = {
        "day": "daily",
        "week": "weekly",
        "month": "monthly",
    }[layer]
    directory = root / "diary" / subdirectory
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{date_value}.md").write_text(
        "---\n"
        f"type: diary\ndate: '{date_value}'\nlayer: {layer}\n"
        f"period: '{date_value}'\npromoted_knowledge: []\n"
        f"---\n{body}\n",
        encoding="utf-8",
    )


def write_profile(root: Path, **dimensions: str) -> None:
    from trowel_py.memory.types import Profile

    MemoryStore(root).write_profile(
        Profile(updated="2026-07-14", **dimensions),
        source="user-edit",
    )


def seed_full_memory(root: Path) -> None:
    write_core(root, [item("a", "CORE_MARKER imperative", "active")])
    write_profile(root, ability="PROFILE_MARKER")
    write_l0(root, "L0_MARKER index")
    write_diary(root, "2026-07-08", "day", "DAY_MARKER")
