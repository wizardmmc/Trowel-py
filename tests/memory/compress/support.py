"""日记压缩测试的共享搭建。"""

from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter
from trowel_py.memory.types import PersistContext


class FakeProvider:
    """返回预设响应并记录调用的最小 provider。"""

    def __init__(
        self,
        response: str = "压缩版",
        *,
        responses: list[str] | None = None,
        raise_on: Exception | None = None,
    ) -> None:
        self.response = response
        self._seq = list(responses) if responses else None
        self.raise_on = raise_on
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_on is not None:
            raise self.raise_on
        if self._seq is not None:
            return self._seq.pop(0)
        return self.response


def structured_episode(
    root: Path,
    sid: str,
    date: str = "2026-07-01",
    *,
    outcomes: tuple[str, ...] = (),
    decisions: tuple[str, ...] = (),
    corrections: tuple[str, ...] = (),
    open_loops: tuple[str, ...] = (),
    events: str = "",
    segment_id: str | None = None,
    registered_at: str = "2026-07-01T10:00:00",
) -> None:
    ctx = PersistContext(
        segment_id=segment_id or f"{sid}:0:end",
        cc_session_id=sid,
        workdir="/tmp",
        registered_at=registered_at,
        review_date=date,
        source_jsonl="/tmp/x.jsonl",
        activity_dates=(date,),
        date_basis="jsonl_timestamp",
        processed_date=date,
    )
    MemoryStore(root).write_episode(
        ctx,
        (
            DraftDiary(
                date=date,
                outcomes=outcomes,
                decisions=decisions,
                corrections=corrections,
                open_loops=open_loops,
                events=events,
            ),
        ),
    )


def items_json(*items: tuple[str, str, str]) -> str:
    return json.dumps(
        {
            "items": [
                {"type": item_type, "text": text, "source": source}
                for item_type, text, source in items
            ]
        }
    )


def daily(root: Path, date: str, body: str) -> None:
    MemoryStore(root).write_diary(
        {
            "type": "diary",
            "date": date,
            "layer": "day",
            "period": date,
            "promoted_knowledge": [],
            "__body": body,
        }
    )


def daily_frontmatter(root: Path, date: str) -> dict | None:
    path = root / "diary" / "daily" / f"{date}.md"
    if not path.exists():
        return None
    frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return frontmatter


def require_daily_frontmatter(root: Path, date: str) -> dict:
    frontmatter = daily_frontmatter(root, date)
    assert frontmatter is not None
    return frontmatter


def write_legacy_episode(
    root: Path,
    sid: str,
    date: str,
    body: str,
) -> None:
    path = root / "episodes" / f"{sid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _dump_frontmatter(
            {
                "type": "episode",
                "cc_session_id": sid,
                "workdir": "/tmp",
                "registered_at": f"{date}T10:00:00",
                "review_date": date,
                "source_jsonl": f"/tmp/{sid}.jsonl",
                "segments": [],
            },
            body,
        ),
        encoding="utf-8",
    )
