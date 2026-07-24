from __future__ import annotations

import json
from pathlib import Path

from tests.memory.daily_review.support import FINISHED, VALID_DRAFT
from trowel_py.memory.provenance import (
    CcJsonlSource,
    CodexTurnsSource,
    CompletedSegment,
    DerivationProvenance,
    completed_segment_from_dict,
    completed_segment_to_dict,
    extract_cc_source_models,
)
from trowel_py.memory.daily_review.agent import _derivation_for_host
from trowel_py.memory.review_job import run_one_session
from trowel_py.memory.sessions_repo import SessionRecord


def test_cc_source_models_only_use_requested_real_jsonl_range(tmp_path: Path) -> None:
    fixture = (
        Path(__file__).parents[2]
        / "cc_host"
        / "fixtures"
        / "bg_taskoutput_completed.jsonl"
    )
    assistant_lines = [
        line
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("type") == "assistant"
    ]
    first = assistant_lines[0] + "\n"
    # 字段 shape 来自同一真实脱敏 fixture，只改模型值以验证范围隔离。
    second = assistant_lines[1].replace('"test-model"', '"second-model"') + "\n"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(first + first + second, encoding="utf-8")
    first_range_end = len((first + first).encode("utf-8"))

    first_range = extract_cc_source_models(transcript, 0, first_range_end)
    whole_file = extract_cc_source_models(transcript, 0, transcript.stat().st_size)

    assert [(item.model, item.basis) for item in first_range] == [
        ("test-model", "runtime_event")
    ]
    assert [item.model for item in whole_file] == ["test-model", "second-model"]


def test_real_codex_thread_turn_builds_host_neutral_segment() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "codex_host"
        / "fixtures"
        / "thread-read-0.144.0.json"
    )
    thread = json.loads(fixture.read_text(encoding="utf-8"))["thread"]
    completed_turn = next(turn for turn in thread["turns"] if turn["status"] == "completed")
    turn_id = completed_turn["id"]

    segment = CompletedSegment(
        segment_id=f"codex:{thread['id']}:{turn_id}",
        host_kind="codex",
        native_session_id=thread["id"],
        session_kind="user",
        workdir=thread["cwd"],
        registered_at="",
        completed_at="",
        source=CodexTurnsSource(turn_ids=(turn_id,)),
    )

    assert segment.host_kind == "codex"
    assert segment.native_session_id == thread["id"]
    assert isinstance(segment.source, CodexTurnsSource)
    assert segment.source.turn_ids == (turn_id,)
    assert completed_segment_from_dict(completed_segment_to_dict(segment)) == segment


def test_generator_keeps_known_effort_when_model_is_unknown() -> None:
    class EffortOnlyHost:
        session_id = "review-run-effort-only"
        model = None
        effort = "high"

    derivation = _derivation_for_host(EffortOnlyHost())

    assert derivation.generator is not None
    assert derivation.generator.model == ""
    assert derivation.generator.effort == "high"
    assert derivation.generator.basis == "host_config"


async def test_review_draft_records_configured_generator(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text('{"type":"user"}\n', encoding="utf-8")
    class ProvenanceHost:
        session_id = "review-run-1"
        model = "glm-5.1"
        effort = "high"

        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir

        async def send(self, _prompt: str):
            (self.workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    session = SessionRecord(
        cc_session_id="cc-source",
        workdir="/project",
        date="2026-07-24",
        jsonl_path=str(source),
        registered_at="2026-07-24T10:00:00",
    )
    captured: list[DerivationProvenance] = []
    await run_one_session(
        session,
        "2026-07-24",
        tmp_path / "memory",
        host_factory=lambda _session, workdir: ProvenanceHost(workdir),
        derivation_sink=captured.append,
    )

    [derivation] = captured
    assert derivation.run_id == "review-run-1"
    assert derivation.generator_runtime == "claude_code"
    assert derivation.generator is not None
    assert derivation.generator.model == "glm-5.1"
    assert derivation.generator.effort == "high"
    assert derivation.generator.basis == "host_config"


def test_completed_segment_keeps_cc_byte_boundary() -> None:
    segment = CompletedSegment(
        segment_id="cc-1:10:20",
        host_kind="claude_code",
        native_session_id="cc-1",
        session_kind="user",
        workdir="/project",
        registered_at="2026-07-24T10:00:00",
        completed_at="2026-07-24T11:00:00",
        source=CcJsonlSource(
            locator="/sessions/cc-1.jsonl",
            start_offset=10,
            end_offset=20,
        ),
    )

    assert isinstance(segment.source, CcJsonlSource)
    assert segment.source.start_offset == 10
    assert segment.source.end_offset == 20
    assert completed_segment_from_dict(completed_segment_to_dict(segment)) == segment
