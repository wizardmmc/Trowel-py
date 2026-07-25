from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.compress import compress_daily

from tests.memory.compress.support import FakeProvider, items_json, structured_episode


def seed_day(root: Path, day: str = "2026-07-06", text: str = "完成 A") -> None:
    structured_episode(root, f"session-{day}", date=day, outcomes=(text,))


def generate_day(root: Path, day: str = "2026-07-06", text: str = "完成 A") -> None:
    compress_daily(root, day, FakeProvider(items_json(("outcome", text, "S1"))))


def weekly_json(day: str = "2026-07-06", text: str = "完成 A") -> str:
    return json.dumps(
        {
            "items": [
                {"type": "outcome", "text": text, "source_days": [day]}
            ],
            "bypass": {},
        }
    )


def successful_provider(
    day: str = "2026-07-06",
    text: str = "完成 A",
) -> FakeProvider:
    return FakeProvider(
        responses=[
            items_json(("outcome", text, "S1")),
            weekly_json(day, text),
            "本月完成 A。",
        ]
    )
