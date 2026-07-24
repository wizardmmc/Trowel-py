from __future__ import annotations

import json
import os
from pathlib import Path

VALID_SESSION_ID = "00000000-0000-4000-8000-000000000001"


def write_events(path: Path, events: list[dict]) -> Path:
    # 合成输入只组合已确认字段，用于排列边界，不证明未知 CC shape。
    with path.open("w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def user_event(text: str, **extra: object) -> dict:
    event: dict = {
        "type": "user",
        "isSidechain": False,
        "message": {"role": "user", "content": text},
    }
    event.update(extra)
    return event


def ai_title_event(title: str) -> dict:
    return {
        "type": "ai-title",
        "aiTitle": title,
    }


def metadata_events(count: int) -> list[dict]:
    return [
        {
            "type": "attachment",
            "uuid": f"attachment-{index}",
        }
        for index in range(count)
    ]


def write_timed_sessions(
    session_dir: Path,
    ids_with_mtime: list[tuple[str, int]],
) -> None:
    for session_id, mtime in ids_with_mtime:
        path = write_events(
            session_dir / f"{session_id}.jsonl",
            [user_event(session_id)],
        )
        os.utime(path, (mtime, mtime))
