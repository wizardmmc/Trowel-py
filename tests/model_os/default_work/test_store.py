from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trowel_py.model_os.default_work import (
    CandidateDraft,
    DefaultWorkError,
    DefaultWorkRepository,
    GenerationUsage,
    RunDefaultPilotCommand,
    SampledSource,
)
from trowel_py.model_os.store import ModelOsStore


def _source(hash_: str = "a" * 64) -> SampledSource:
    return SampledSource(
        uri_at_generation="memory://notes/a",
        memory_id="memory-a",
        sampled_content_hash=hash_,
        updated="2026-07-25",
        chars=20,
        text="private transient text",
    )


def _draft(content: str = "link") -> CandidateDraft:
    return CandidateDraft(
        content=content,
        source_refs=("memory://notes/a",),
        related_question="question",
        why_useful="useful",
        verification="verify",
        uncertainty="uncertain",
    )


@pytest.fixture
def repo(tmp_path):
    store = ModelOsStore(tmp_path / "model-os.db")
    store.open()
    yield DefaultWorkRepository(store)
    store.close()


def test_command_replay_and_successful_source_generation_dedupe(repo) -> None:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    command = RunDefaultPilotCommand("command-1", "codex", ("memory://notes/a",))
    begun = repo.begin(command, (_source(),), occurred_at=now)
    result = repo.commit_success(
        begun.generation_id,
        (_draft(),),
        episode_id="episode-1",
        effective_model="deep-model",
        usage=GenerationUsage(10, 5, 2.0, None),
        occurred_at=now,
    )

    replay = repo.begin(command, (_source(),), occurred_at=now)
    assert replay.result == result
    assert "private transient text" not in repo.journal_payload(command.command_id)
    journal = [
        event
        for _, event in repo._store.list_events()
        if event.correlation_id is not None
        and event.correlation_id.startswith("command.default.")
    ]
    assert [event.kind for event in journal] == [
        "command.intent",
        "command.result",
    ]
    assert "private transient text" not in str(journal[0].payload)
    assert journal[1].payload["candidate_ids"] == list(result.candidate_ids)

    deduped = repo.begin(
        RunDefaultPilotCommand("command-2", "codex", ("memory://notes/a",)),
        (_source(),),
        occurred_at=now,
    )
    assert deduped.result == result


def test_same_command_id_with_different_input_conflicts_without_new_generation(
    repo,
) -> None:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    repo.begin(
        RunDefaultPilotCommand("same", "codex", ("memory://notes/a",)),
        (_source(),),
        occurred_at=now,
    )
    with pytest.raises(DefaultWorkError) as raised:
        repo.begin(
            RunDefaultPilotCommand("same", "claude_code", ("memory://notes/a",)),
            (_source(),),
            occurred_at=now,
        )
    assert raised.value.code == "idempotency_conflict"
    assert repo.generation_count() == 1


def test_candidate_commit_is_atomic_and_exact_duplicates_are_audit_only(repo) -> None:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    begun = repo.begin(
        RunDefaultPilotCommand("one", "codex", ("memory://notes/a",)),
        (_source("a" * 64),),
        occurred_at=now,
    )
    result = repo.commit_success(
        begun.generation_id,
        (_draft("A useful link"), _draft("  Ａ USEFUL\n link ")),
        episode_id="ep-1",
        effective_model="model",
        usage=GenerationUsage(1, 1, 1.0, None),
        occurred_at=now,
    )
    assert len(result.candidates) == 1
    assert repo.candidate_count() == 1
    assert repo.duplicate_count() == 1


def test_outcomes_are_idempotent_terminal_and_gate_never_enables_automatic(
    repo,
) -> None:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    candidate_ids = []
    for index in range(20):
        source = _source(f"{index:064x}")
        begun = repo.begin(
            RunDefaultPilotCommand(f"run-{index}", "codex", ("memory://notes/a",)),
            (source,),
            occurred_at=now,
        )
        result = repo.commit_success(
            begun.generation_id,
            (_draft(f"claim {index}"),),
            episode_id=f"ep-{index}",
            effective_model="model",
            usage=GenerationUsage(2, 1, 1.0, None),
            occurred_at=now,
        )
        candidate_ids.append(result.candidates[0].candidate_id)
    for index, candidate_id in enumerate(candidate_ids):
        outcome = "invalid" if index < 2 else "adopted"
        reason = "wrong" if outcome == "invalid" else None
        first = repo.record_outcome(
            command_id=f"out-{index}",
            candidate_id=candidate_id,
            outcome=outcome,
            reason=reason,
            occurred_at=now,
        )
        assert (
            repo.record_outcome(
                command_id=f"out-{index}",
                candidate_id=candidate_id,
                outcome=outcome,
                reason=reason,
                occurred_at=now,
            )
            == first
        )
    with pytest.raises(DefaultWorkError) as raised:
        repo.record_outcome(
            command_id="change",
            candidate_id=candidate_ids[0],
            outcome="dismissed",
            reason=None,
            occurred_at=now,
        )
    assert raised.value.code == "candidate_terminal"

    report = repo.gate_report()
    assert report.status == "reevaluation_ready"
    assert report.outcome_count == 20
    assert report.adoption_rate == 0.9
    assert report.invalid_rate == 0.1
    assert report.automatic_default is False


def test_failed_command_replay_returns_same_error_without_new_generation(repo) -> None:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    command = RunDefaultPilotCommand("failed-command", "codex", ("memory://notes/a",))
    begun = repo.begin(command, (_source(),), occurred_at=now)
    repo.commit_failure(
        begun.generation_id,
        "output_schema_invalid",
        episode_id="episode-1",
        usage=GenerationUsage(1, 1, 1.0, None),
        occurred_at=now,
    )

    with pytest.raises(DefaultWorkError) as raised:
        repo.begin(command, (_source(),), occurred_at=now)
    assert raised.value.code == "output_schema_invalid"
    assert repo.generation_count() == 1
