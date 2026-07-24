from __future__ import annotations

import json
from pathlib import Path

from tests.memory.daily_review.support import FINISHED, FakeHost
from trowel_py.memory.review_job import run_daily_review
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)


def _register_turn(memory_root: Path, journal_path: Path) -> None:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(
        json.dumps(
            {
                "schema": "codex-event-v1",
                "type": "user",
                "thread_id": "thread-review",
                "turn_id": "turn-review",
                "payload": {"text": "review this turn"},
                "timestamp": "2026-07-09T10:00:00+08:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        repo.register_codex_turn(
            thread_id="thread-review",
            turn_id="turn-review",
            trowel_session_id="trowel-review",
            workdir="/workspace",
            journal_path=str(journal_path),
            registered_at="2026-07-09T10:00:00",
            model="gpt-5.6-sol",
            effort="high",
            provider="openai",
            memory_enabled=True,
            profile_enabled=False,
        )
        repo.complete_codex_turn(
            "thread-review",
            "turn-review",
            status="completed",
            completed_at="2026-07-09T10:05:00",
        )
    finally:
        conn.close()


class ReviewHost(FakeHost):
    session_id = "review-run-codex"
    model = "glm-5.1"
    effort = "high"


def _host_factory(_session: SessionRecord, workdir: Path) -> ReviewHost:
    (workdir / "draft.json").write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "Codex 会后结论",
                        "verification": "event-data-supported",
                    }
                ],
                "diary": [
                    {
                        "date": "2026-07-09",
                        "items": [
                            {
                                "kind": "outcome",
                                "summary": "完成 Codex turn 会后提炼",
                                "detail": "",
                                "source_refs": ["L000001"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return ReviewHost([FINISHED])


async def test_completed_codex_turn_uses_shared_memory_persist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory_root = tmp_path / "memory"
    _register_turn(memory_root, tmp_path / "journals" / "turn.jsonl")
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._resolve_provider",
        lambda _provider: None,
    )

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        eligible_before="2026-07-10T00:00:00",
        host_factory=_host_factory,
    )

    manifest_path = (
        memory_root
        / "meta"
        / "persisted-segments"
        / "codex:thread-review:turn-review.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["host_kind"] == "codex"
    assert manifest["source"]["source"] == {
        "kind": "codex_turns",
        "turn_ids": ["turn-review"],
    }
    assert manifest["source"]["source_models"] == [
        {
            "model": "gpt-5.6-sol",
            "effort": "high",
            "provider": "openai",
            "basis": "binding",
        }
    ]
    assert manifest["derivation"]["run_id"] == "review-run-codex"
    conn = open_sessions_db(memory_root)
    try:
        assert create_sessions_repository(conn).find_incremental_codex() == []
    finally:
        conn.close()


async def test_codex_persist_failure_does_not_advance_turn_watermark(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory_root = tmp_path / "memory"
    _register_turn(memory_root, tmp_path / "journals" / "turn.jsonl")
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._resolve_provider",
        lambda _provider: None,
    )

    def fail_persist(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(
        "trowel_py.memory.daily_review.codex.persist_draft",
        fail_persist,
    )
    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        eligible_before="2026-07-10T00:00:00",
        host_factory=_host_factory,
    )

    conn = open_sessions_db(memory_root)
    try:
        pending = create_sessions_repository(conn).find_incremental_codex()
    finally:
        conn.close()
    assert [item.turn.turn_id for item in pending] == ["turn-review"]
