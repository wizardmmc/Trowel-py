from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from tests.memory.daily_review.support import FINISHED, VALID_DRAFT
from trowel_py.memory.draft import Draft, DraftDiary, DraftNote
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.provenance import (
    CcJsonlSource,
    CompletedSegment,
    DerivationProvenance,
    ModelIdentity,
)
from trowel_py.memory.store import MemoryStore, _split_frontmatter
from trowel_py.memory.types import PersistContext
from trowel_py.memory.review_job import run_daily_review
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)


def _segment(segment_id: str) -> CompletedSegment:
    _sid, start, end = segment_id.split(":")
    return CompletedSegment(
        segment_id=segment_id,
        host_kind="claude_code",
        native_session_id="s1",
        trowel_session_ids=("trowel-1",),
        session_kind="user",
        workdir="/proj",
        registered_at="2026-07-24T10:00:00",
        completed_at="2026-07-24T11:00:00",
        source=CcJsonlSource(
            locator="/jsonl/s1.jsonl",
            start_offset=int(start),
            end_offset=int(end),
        ),
        source_models=(
            ModelIdentity(model="glm-5.2", basis="runtime_event"),
        ),
    )


def _derivation(run_id: str) -> DerivationProvenance:
    return DerivationProvenance(
        pipeline="memory.refine",
        pipeline_version=1,
        run_id=run_id,
        generated_at="2026-07-24T12:00:00",
        generator_runtime="claude_code",
        generator=ModelIdentity(
            model="glm-5.1",
            effort="high",
            basis="host_config",
        ),
    )


def _context(segment_id: str, run_id: str) -> PersistContext:
    source = _segment(segment_id)
    assert isinstance(source.source, CcJsonlSource)
    return PersistContext(
        segment_id=segment_id,
        cc_session_id="s1",
        workdir="/proj",
        registered_at="2026-07-24T10:00:00",
        review_date="2026-07-24",
        source_jsonl="/jsonl/s1.jsonl",
        source_start_offset=source.source.start_offset,
        source_end_offset=source.source.end_offset,
        completed_segment=source,
        derivation=_derivation(run_id),
    )


def _draft() -> Draft:
    return Draft(
        notes=(
            DraftNote(
                title="可追溯结论",
                body="正文",
                verification="event-data-supported",
            ),
        ),
        diary=(DraftDiary(date="2026-07-24", outcomes=("完成来源接入",)),),
        reflection="这次使用了已有记忆",
        escalate_to_human=("仍需人工确认模型选择",),
    )


async def test_cc_daily_review_persists_source_and_generator_end_to_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = (
        Path(__file__).parents[2]
        / "cc_host"
        / "fixtures"
        / "bg_taskoutput_completed.jsonl"
    )
    transcript = tmp_path / "cc-session.jsonl"
    transcript.write_bytes(fixture.read_bytes())
    memory_root = tmp_path / "memory"

    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(
        SessionRecord(
            cc_session_id="cc-e2e",
            trowel_session_id="trowel-e2e",
            workdir="/project",
            date="2026-07-09",
            jsonl_path=str(transcript),
            registered_at="2026-07-09T10:00:00",
        )
    )
    repo.update_completed(
        "cc-e2e",
        transcript.stat().st_size,
        "2026-07-09T11:00:00",
    )
    conn.close()

    class ReviewHost:
        session_id = "review-e2e"
        model = "glm-5.1"
        effort = "high"

        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir

        async def send(self, _prompt: str):
            (self.workdir / "draft.json").write_text(
                VALID_DRAFT,
                encoding="utf-8",
            )
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._resolve_provider",
        lambda _provider: None,
    )
    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=lambda _session, workdir: ReviewHost(workdir),
    )

    manifest_path = memory_root / "meta/persisted-segments" / (
        f"cc-e2e:0:{transcript.stat().st_size}.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["trowel_session_ids"] == ["trowel-e2e"]
    assert manifest["source"]["source_models"][0]["model"] == "test-model"
    assert manifest["derivation"]["run_id"] == "review-e2e"
    assert manifest["derivation"]["generator"]["model"] == "glm-5.1"


def test_persisted_artifacts_carry_source_and_derivation(tmp_path: Path) -> None:
    context = _context("s1:0:100", "review-run-1")
    report = persist_draft(MemoryStore(tmp_path), _draft(), context)

    episode_fm, _ = _split_frontmatter(
        (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    )
    assert episode_fm is not None
    assert episode_fm["host_kind"] == "claude_code"
    assert episode_fm["native_session_id"] == "s1"
    episode_segment = episode_fm["segments"][0]
    assert episode_segment["source"]["kind"] == "cc_jsonl"
    assert episode_segment["source_models"][0]["model"] == "glm-5.2"
    assert episode_segment["derivation"]["run_id"] == "review-run-1"

    [note_path] = (tmp_path / "notes").glob("*.md")
    note_fm, _ = _split_frontmatter(note_path.read_text(encoding="utf-8"))
    assert note_fm is not None
    assert note_fm["source_segments"] == ["s1:0:100"]
    assert note_fm["derivations"][0]["generator"]["model"] == "glm-5.1"
    loaded_note = MemoryStore(tmp_path).load_note(note_path.stem)
    assert loaded_note is not None
    assert loaded_note.derivations == (context.derivation,)

    for rel in (
        "meta/reflections/s1.md",
        "meta/escalations/s1.md",
    ):
        artifact_fm, _ = _split_frontmatter((tmp_path / rel).read_text(encoding="utf-8"))
        assert artifact_fm is not None
        assert artifact_fm["source"]["segment_id"] == "s1:0:100"
        assert artifact_fm["derivation"]["run_id"] == "review-run-1"

    assert report.manifest_path is not None
    manifest = json.loads((tmp_path / report.manifest_path).read_text(encoding="utf-8"))
    assert manifest["source"]["segment_id"] == "s1:0:100"
    assert manifest["derivation"]["pipeline"] == "memory.refine"


def test_note_merges_distinct_source_segments_and_derivations(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _context("s1:0:100", "review-run-1"))
    persist_draft(store, _draft(), _context("s1:100:200", "review-run-2"))

    [note_path] = (tmp_path / "notes").glob("*.md")
    note_fm, _ = _split_frontmatter(note_path.read_text(encoding="utf-8"))
    assert note_fm is not None
    assert note_fm["source_segments"] == ["s1:0:100", "s1:100:200"]
    assert [item["run_id"] for item in note_fm["derivations"]] == [
        "review-run-1",
        "review-run-2",
    ]


def test_legacy_context_still_persists_without_new_provenance(tmp_path: Path) -> None:
    legacy = replace(
        _context("s1:0:100", "review-run-1"),
        completed_segment=None,
        derivation=None,
    )
    report = persist_draft(MemoryStore(tmp_path), _draft(), legacy)

    assert report.ok
    episode_fm, _ = _split_frontmatter(
        (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    )
    assert episode_fm is not None
    assert "host_kind" not in episode_fm
    assert "source" not in episode_fm["segments"][0]
