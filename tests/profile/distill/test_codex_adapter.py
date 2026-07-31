"""Codex turn 到 Profile 提炼候选、来源和证据的适配测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.memory.sessions_repo import CodexTurnRecord
from trowel_py.profile.distill.adapters.codex import build_codex_backlog
from trowel_py.profile.distill.gate import DistillError
from trowel_py.profile.distill.sources.codex import build_codex_distill_source
from trowel_py.profile.distill.sources.evidence import (
    build_target_evidence_validator,
)
from trowel_py.profile.distill.state import ProcessedCodexTurn


def _turn(
    tmp_path: Path,
    turn_id: str,
    *,
    thread_id: str = "thread-a",
    completed_at: str | None = "2026-07-31T10:05:00",
    registered_at: str = "2026-07-31T10:00:00",
    session_kind: str = "user",
    extracted_at: str | None = None,
) -> CodexTurnRecord:
    return CodexTurnRecord(
        thread_id=thread_id,
        turn_id=turn_id,
        trowel_session_id=f"trowel-{turn_id}",
        workdir="/workspace",
        journal_path=str(tmp_path / f"{turn_id}.jsonl"),
        registered_at=registered_at,
        status="completed" if completed_at else "running",
        completed_at=completed_at,
        extracted_at=extracted_at,
        session_kind=session_kind,
    )


def _write_normalized_journal(path: Path, user_text: str) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema": "codex-event-v1",
                        "type": "user",
                        "payload": {"text": user_text},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "schema": "codex-event-v1",
                        "type": "finished",
                        "payload": {"status": "completed"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_backlog_uses_profile_records_not_memory_extracted_at(
    tmp_path: Path,
) -> None:
    first = _turn(
        tmp_path,
        "turn-1",
        completed_at="2026-07-31T10:05:00",
        extracted_at="2026-07-31T11:00:00",
    )
    second = _turn(
        tmp_path,
        "turn-2",
        completed_at="2026-07-31T10:10:00",
        registered_at="2026-07-31T10:06:00",
    )
    processed = {
        ("thread-a", "turn-2"): ProcessedCodexTurn(
            "thread-a",
            "turn-2",
            "2026-07-31T12:00:00",
        )
    }

    backlog = build_codex_backlog([first, second], processed)

    assert [candidate.source_id for candidate in backlog] == ["codex:thread-a:turn-1"]


def test_backlog_builds_each_turn_with_all_earlier_thread_context(
    tmp_path: Path,
) -> None:
    first = _turn(
        tmp_path,
        "turn-1",
        completed_at="2026-07-31T10:05:00",
    )
    second = _turn(
        tmp_path,
        "turn-2",
        completed_at="2026-07-31T10:10:00",
        registered_at="2026-07-31T10:06:00",
    )
    third = _turn(
        tmp_path,
        "turn-3",
        thread_id="thread-b",
        completed_at="2026-07-31T10:07:00",
        registered_at="2026-07-31T10:01:00",
    )

    backlog = build_codex_backlog([first, third, second], {})
    by_id = {candidate.turn.turn_id: candidate for candidate in backlog}

    assert by_id["turn-1"].history == ()
    assert [turn.turn_id for turn in by_id["turn-2"].history] == ["turn-1"]
    assert by_id["turn-3"].history == ()


def test_source_separates_earlier_turns_from_the_current_target(
    tmp_path: Path,
) -> None:
    first = _turn(tmp_path, "turn-1")
    second = _turn(
        tmp_path,
        "turn-2",
        completed_at="2026-07-31T10:10:00",
    )

    source = build_codex_distill_source(second, (first,))

    assert source.runtime == "codex"
    assert source.source_id == "codex:thread-a:turn-2"
    assert [item.path for item in source.context] == [first.journal_path]
    assert [item.path for item in source.target] == [second.journal_path]


def test_target_evidence_rejects_context_only_text(tmp_path: Path) -> None:
    first = _turn(tmp_path, "turn-1")
    second = _turn(
        tmp_path,
        "turn-2",
        completed_at="2026-07-31T10:10:00",
    )
    _write_normalized_journal(Path(first.journal_path), "我喜欢上下文里的做法")
    _write_normalized_journal(Path(second.journal_path), "真实运行比只看单测更可信")
    source = build_codex_distill_source(second, (first,))

    validator = build_target_evidence_validator(source)

    assert validator is not None
    assert validator("真实运行比只看单测更可信")
    assert validator("只看单测")
    assert not validator("我喜欢上下文里的做法")


def test_target_evidence_reads_legacy_native_rollout_user_message(
    tmp_path: Path,
) -> None:
    turn = _turn(tmp_path, "legacy-turn")
    Path(turn.journal_path).write_text(
        json.dumps(
            {
                "timestamp": "2026-07-01T10:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "解释时第一次出现的名词要讲清楚",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    source = build_codex_distill_source(turn, ())

    validator = build_target_evidence_validator(source)

    assert validator is not None
    assert validator("第一次出现的名词要讲清楚")


def test_target_evidence_rejects_journal_without_user_event(
    tmp_path: Path,
) -> None:
    turn = _turn(tmp_path, "missing-user")
    Path(turn.journal_path).write_text(
        json.dumps(
            {
                "schema": "codex-event-v1",
                "type": "finished",
                "payload": {"status": "completed"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = build_codex_distill_source(turn, ())

    with pytest.raises(DistillError, match="contains no user event"):
        build_target_evidence_validator(source)
