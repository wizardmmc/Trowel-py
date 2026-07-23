from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from trowel_py.memory.sessions_repo import SessionRecord

FINISHED = SimpleNamespace(type="finished")
ERROR = SimpleNamespace(type="error")
VALID_DRAFT = json.dumps(
    {
        "notes": [{"title": "结论", "verification": "verified"}],
        "diary": [{"date": "2026-07-09", "outcomes": ["完成事件提炼"]}],
    }
)

_default_jsonl_path = ""


class FakeHost:
    def __init__(self, events: list) -> None:
        self._events = events

    async def send(self, prompt: str):
        for event in self._events:
            yield event

    async def close(self) -> None:
        pass


def factory(events: list, draft_text: str | None = None):
    def create_host(session: SessionRecord, workdir: Path) -> FakeHost:
        if draft_text is not None:
            (workdir / "draft.json").write_text(draft_text, encoding="utf-8")
        return FakeHost(events)

    return create_host


def prepare_default_jsonl(path: Path) -> None:
    global _default_jsonl_path

    # 多个增量用例使用 4096 内的任意 offset，数据必须覆盖整个区间。
    path.write_text(
        (
            json.dumps(
                {"type": "user", "timestamp": "2026-07-09T02:00:00.000Z"}
            )
            + "\n"
        )
        * 100,
        encoding="utf-8",
    )
    _default_jsonl_path = str(path)


def session(sid: str = "s1", workdir: str = "/proj") -> SessionRecord:
    return SessionRecord(
        cc_session_id=sid,
        workdir=workdir,
        date="2026-07-09",
        jsonl_path=_default_jsonl_path,
        registered_at="2026-07-09T10:00:00",
    )


def write_jsonl(path: Path, timestamps: list[str]) -> int:
    lines = [json.dumps({"type": "user", "timestamp": ts}) for ts in timestamps]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.stat().st_size
