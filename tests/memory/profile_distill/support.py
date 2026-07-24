from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)

FINISHED = SimpleNamespace(type="finished")
ERROR = SimpleNamespace(type="error")

VALID_DRAFT = json.dumps(
    {
        "suggestions": [
            {
                "dimension": "ability",
                "body": "熟悉缓存一致性 / 并发调试",
                "sources": ["用户提到缓存失效排查"],
                "rationale": "自述技术经验",
            }
        ]
    }
)


class FakeHost:
    def __init__(self, events: list) -> None:
        self._events = events

    async def send(self, prompt: str):
        for event in self._events:
            yield event

    async def close(self) -> None:
        pass


def fake_host_factory(events: list, draft_text: str | None = None):
    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        if draft_text is not None:
            (workdir / "suggestions-draft.json").write_text(
                draft_text, encoding="utf-8"
            )
        return FakeHost(events)

    return factory


def session_record(sid: str = "s1") -> SessionRecord:
    return SessionRecord(
        cc_session_id=sid,
        workdir="/proj",
        date="2026-07-14",
        jsonl_path="/x.jsonl",
        registered_at="2026-07-14T10:00:00",
    )


def seed_session(root: Path, sid: str = "s1", completed: int = 1000) -> None:
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.register(session_record(sid))
        repo.update_completed(sid, completed)
    finally:
        conn.close()


def draft_json(items: list) -> str:
    return json.dumps({"suggestions": items})


def draft_item(
    body: str = "短结论",
    sources: object = None,
    dim: str = "ability",
) -> dict:
    return {
        "dimension": dim,
        "body": body,
        "sources": ["用户原话"] if sources is None else sources,
        "rationale": "证据",
    }
