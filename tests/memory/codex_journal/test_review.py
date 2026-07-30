from __future__ import annotations

import json
from pathlib import Path

from tests.memory.daily_review.support import FINISHED, FakeHost
from trowel_py.memory.review_job import run_daily_review
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    SessionsRepository,
    create_sessions_repository,
    open_sessions_db,
)


def _register_turn(
    memory_root: Path,
    journal_path: Path,
    *,
    thread_id: str = "thread-review",
    turn_id: str = "turn-review",
    trowel_session_id: str = "trowel-review",
    completed_at: str = "2026-07-09T10:05:00",
) -> None:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(
        json.dumps(
            {
                "schema": "codex-event-v1",
                "type": "user",
                "thread_id": thread_id,
                "turn_id": turn_id,
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
            thread_id=thread_id,
            turn_id=turn_id,
            trowel_session_id=trowel_session_id,
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
            thread_id,
            turn_id,
            status="completed",
            completed_at=completed_at,
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
    assert [item.turn_ids for item in pending] == [("turn-review",)]


async def test_same_thread_turns_use_one_refine_and_one_judge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory_root = tmp_path / "memory"
    first_path = tmp_path / "journals" / "turn-1.jsonl"
    second_path = tmp_path / "journals" / "turn-2.jsonl"
    _register_turn(
        memory_root,
        first_path,
        turn_id="turn-1",
        trowel_session_id="trowel-1",
        completed_at="2026-07-09T10:05:00",
    )
    _register_turn(
        memory_root,
        second_path,
        turn_id="turn-2",
        trowel_session_id="trowel-2",
        completed_at="2026-07-09T10:10:00",
    )
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._resolve_provider",
        lambda _provider: None,
    )
    host_creations: list[Path] = []

    def counting_factory(session: SessionRecord, workdir: Path) -> ReviewHost:
        host_creations.append(workdir)
        return _host_factory(session, workdir)

    conn = open_sessions_db(memory_root)
    try:
        [fragment] = create_sessions_repository(conn).find_incremental_codex(
            completed_before="2026-07-10T00:00:00"
        )
    finally:
        conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        eligible_before="2026-07-10T00:00:00",
        host_factory=counting_factory,
    )

    assert len(host_creations) == 2
    manifest_path = (
        memory_root / "meta" / "persisted-segments" / f"{fragment.fragment_id}.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["source"] == {
        "kind": "codex_turns",
        "turn_ids": ["turn-1", "turn-2"],
    }
    conn = open_sessions_db(memory_root)
    try:
        assert create_sessions_repository(conn).find_incremental_codex() == []
    finally:
        conn.close()


async def test_incremental_codex_review_separates_extracted_history_from_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory_root = tmp_path / "memory"
    history_path = tmp_path / "journals" / "turn-history.jsonl"
    target_path = tmp_path / "journals" / "turn-target.jsonl"
    _register_turn(
        memory_root,
        history_path,
        turn_id="turn-history",
        completed_at="2026-07-09T09:05:00",
    )
    conn = open_sessions_db(memory_root)
    try:
        create_sessions_repository(conn).advance_codex_extracted(
            "thread-review",
            "turn-history",
            when="2026-07-09T09:10:00",
        )
    finally:
        conn.close()
    _register_turn(
        memory_root,
        target_path,
        turn_id="turn-target",
        completed_at="2026-07-09T10:05:00",
    )
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._resolve_provider",
        lambda _provider: None,
    )
    prompts: list[str] = []

    class CapturingHost(ReviewHost):
        async def send(self, prompt: str):
            prompts.append(prompt)
            yield FINISHED

    def capturing_factory(
        _session: SessionRecord,
        workdir: Path,
    ) -> CapturingHost:
        draft_name = (
            "judgement-draft.json"
            if workdir.parent.parent.name == "judge-work"
            else "draft.json"
        )
        draft_text = (
            json.dumps({"hits": [], "recall_miss": [], "summary": "ok"})
            if draft_name == "judgement-draft.json"
            else json.dumps(
                {
                    "diary": [
                        {
                            "date": "2026-07-09",
                            "items": [
                                {
                                    "kind": "outcome",
                                    "summary": "完成增量提炼",
                                    "detail": "",
                                }
                            ],
                        }
                    ]
                }
            )
        )
        (workdir / draft_name).write_text(draft_text, encoding="utf-8")
        return CapturingHost([FINISHED])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        eligible_before="2026-07-10T00:00:00",
        host_factory=capturing_factory,
    )

    assert len(prompts) == 2
    for prompt in prompts:
        assert "历史上下文" in prompt
        assert "本次处理目标" in prompt
        assert str(history_path) in prompt
        assert str(target_path) in prompt
        assert prompt.index(str(history_path)) < prompt.index(str(target_path))

    manifest_path = (
        memory_root
        / "meta"
        / "persisted-segments"
        / "codex:thread-review:turn-target.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["source"] == {
        "kind": "codex_turns",
        "turn_ids": ["turn-target"],
    }


async def test_missing_journal_keeps_whole_codex_fragment_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory_root = tmp_path / "memory"
    first_path = tmp_path / "journals" / "turn-1.jsonl"
    second_path = tmp_path / "journals" / "turn-2.jsonl"
    _register_turn(memory_root, first_path, turn_id="turn-1")
    _register_turn(
        memory_root,
        second_path,
        turn_id="turn-2",
        completed_at="2026-07-09T10:10:00",
    )
    second_path.unlink()
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._resolve_provider",
        lambda _provider: None,
    )

    def unexpected_factory(_session: SessionRecord, _workdir: Path) -> ReviewHost:
        raise AssertionError("missing fragment source must not start an agent")

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        eligible_before="2026-07-10T00:00:00",
        host_factory=unexpected_factory,
    )

    conn = open_sessions_db(memory_root)
    try:
        [pending] = create_sessions_repository(conn).find_incremental_codex()
    finally:
        conn.close()
    assert pending.turn_ids == ("turn-1", "turn-2")
    assert not (memory_root / "meta" / "persisted-segments").exists()


async def test_manifest_before_watermark_retry_does_not_duplicate_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory_root = tmp_path / "memory"
    _register_turn(
        memory_root,
        tmp_path / "journals" / "turn-1.jsonl",
        turn_id="turn-1",
    )
    _register_turn(
        memory_root,
        tmp_path / "journals" / "turn-2.jsonl",
        turn_id="turn-2",
        completed_at="2026-07-09T10:10:00",
    )
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._resolve_provider",
        lambda _provider: None,
    )
    original_advance = SessionsRepository.advance_codex_extracted_many
    attempts = 0

    def fail_once(
        self: SessionsRepository,
        thread_id: str,
        turn_ids: tuple[str, ...],
        *,
        when: str | None = None,
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("simulated post-manifest failure")
        original_advance(self, thread_id, turn_ids, when=when)

    monkeypatch.setattr(
        SessionsRepository,
        "advance_codex_extracted_many",
        fail_once,
    )

    for _ in range(2):
        await run_daily_review(
            memory_root=memory_root,
            date_str="2026-07-09",
            eligible_before="2026-07-10T00:00:00",
            host_factory=_host_factory,
        )

    assert attempts == 2
    assert len(list((memory_root / "meta" / "persisted-segments").glob("*.json"))) == 1
    assert len(list((memory_root / "notes").glob("*.md"))) == 1
    conn = open_sessions_db(memory_root)
    try:
        assert create_sessions_repository(conn).find_incremental_codex() == []
    finally:
        conn.close()
