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
    def factory(source_id: str, workdir: Path) -> FakeHost:
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
        repo.claude.register(session_record(sid))
        repo.claude.update_completed(sid, completed)
    finally:
        conn.close()


def seed_codex_turn(
    root: Path,
    *,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    completed_at: str | None = "2026-07-15T10:05:00",
    registered_at: str = "2026-07-15T10:00:00",
    session_kind: str = "user",
    profile_enabled: bool = True,
    memory_extracted_at: str | None = None,
    user_text: str = "真实运行比只看单测更可信",
) -> Path:
    """登记一个 Codex turn，并写入当前 normalized journal shape。"""
    journal_path = root / "fixtures" / thread_id / f"{turn_id}.jsonl"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema": "codex-event-v1",
                        "type": "user",
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "payload": {"text": user_text},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "schema": "codex-event-v1",
                        "type": "finished",
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "payload": {"status": "completed"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.codex.register_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            trowel_session_id=f"trowel-{turn_id}",
            workdir="/proj",
            journal_path=str(journal_path),
            registered_at=registered_at,
            model="gpt-5.6-sol",
            effort="high",
            provider="openai",
            memory_enabled=True,
            profile_enabled=profile_enabled,
            session_kind=session_kind,
        )
        if completed_at is not None:
            repo.codex.complete_turn(
                thread_id,
                turn_id,
                status="completed",
                completed_at=completed_at,
            )
        if memory_extracted_at is not None:
            conn.execute(
                "UPDATE codex_turns SET extracted_at = ?"
                " WHERE thread_id = ? AND turn_id = ?",
                (memory_extracted_at, thread_id, turn_id),
            )
            conn.commit()
    finally:
        conn.close()
    return journal_path


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
