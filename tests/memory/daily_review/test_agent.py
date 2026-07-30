from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.daily_review.support import (
    ERROR,
    FINISHED,
    VALID_DRAFT,
    factory,
    review_source,
    session,
)
from trowel_py.memory.daily_review.sources import JournalSlice, ReviewSource
from trowel_py.memory.review_job import DistillError, run_one_session
from trowel_py.memory.sessions_repo import SessionRecord


async def test_run_one_session_reads_draft(tmp_path: Path) -> None:
    draft = await run_one_session(
        session(),
        "2026-07-09",
        tmp_path / "memory",
        review_source=review_source(),
        host_factory=factory([FINISHED], VALID_DRAFT),
    )
    assert len(draft.notes) == 1
    assert draft.notes[0].verification == "verified"


async def test_codex_fragment_passes_ordered_source_paths_without_combining(
    tmp_path: Path,
) -> None:
    first = tmp_path / "journals" / "turn-1.jsonl"
    second = tmp_path / "journals" / "turn-2.jsonl"
    first.parent.mkdir(parents=True)
    for path, turn_id in ((first, "turn-1"), (second, "turn-2")):
        path.write_text(
            json.dumps(
                {
                    "schema": "codex-event-v1",
                    "type": "user",
                    "turn_id": turn_id,
                    "payload": {"text": turn_id},
                }
            )
            + "\n",
            encoding="utf-8",
        )
    prompts: list[str] = []
    review_workdirs: list[Path] = []

    class CapturingHost:
        async def send(self, prompt: str):
            prompts.append(prompt)
            yield FINISHED

        async def close(self) -> None:
            pass

    def create_host(_session: SessionRecord, workdir: Path) -> CapturingHost:
        review_workdirs.append(workdir)
        (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
        return CapturingHost()

    source_session = SessionRecord(
        cc_session_id="thread-1",
        workdir="/workspace",
        date="2026-07-09",
        jsonl_path=str(first),
        registered_at="2026-07-09T10:00:00",
    )
    await run_one_session(
        source_session,
        "2026-07-09",
        tmp_path / "memory",
        review_source=ReviewSource(
            host_kind="codex",
            context=(),
            target=(JournalSlice(str(first)), JournalSlice(str(second))),
        ),
        host_factory=create_host,
    )

    assert prompts[0].index(str(first)) < prompts[0].index(str(second))
    assert "本次处理目标" in prompts[0]
    assert {
        path.name for path in review_workdirs[0].iterdir() if path.name != "draft.json"
    } == set()


async def test_missing_history_context_is_omitted_without_blocking_target(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    missing_history = tmp_path / "journals" / "missing-history.jsonl"
    target = tmp_path / "journals" / "target.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text('{"type":"user"}\n', encoding="utf-8")
    prompts: list[str] = []

    class CapturingHost:
        async def send(self, prompt: str):
            prompts.append(prompt)
            yield FINISHED

        async def close(self) -> None:
            pass

    def create_host(_session: SessionRecord, workdir: Path) -> CapturingHost:
        (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
        return CapturingHost()

    draft = await run_one_session(
        SessionRecord(
            cc_session_id="thread-context-gap",
            workdir="/workspace",
            date="2026-07-09",
            jsonl_path=str(target),
            registered_at="2026-07-09T10:00:00",
        ),
        "2026-07-09",
        tmp_path / "memory",
        review_source=ReviewSource(
            host_kind="codex",
            context=(JournalSlice(str(missing_history)),),
            target=(JournalSlice(str(target)),),
        ),
        host_factory=create_host,
    )

    assert draft.notes
    assert str(missing_history) not in prompts[0]
    assert str(target) in prompts[0]
    assert "history context incomplete" in caplog.text


async def test_review_cost_only_counts_target_sources(tmp_path: Path) -> None:
    history = tmp_path / "journals" / "history.jsonl"
    target = tmp_path / "journals" / "target.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        '{"type":"assistant","message":{"usage":{"input_tokens":1000,'
        '"output_tokens":100}}}\n',
        encoding="utf-8",
    )
    target.write_text(
        '{"type":"assistant","message":{"usage":{"input_tokens":10,'
        '"output_tokens":5}}}\n',
        encoding="utf-8",
    )
    prompts: list[str] = []

    class CapturingHost:
        async def send(self, prompt: str):
            prompts.append(prompt)
            yield FINISHED

        async def close(self) -> None:
            pass

    def create_host(_session: SessionRecord, workdir: Path) -> CapturingHost:
        (workdir / "draft.json").write_text(VALID_DRAFT, encoding="utf-8")
        return CapturingHost()

    await run_one_session(
        SessionRecord(
            cc_session_id="cost-target-only",
            workdir="/workspace",
            date="2026-07-09",
            jsonl_path=str(target),
            registered_at="2026-07-09T10:00:00",
        ),
        "2026-07-09",
        tmp_path / "memory",
        review_source=ReviewSource(
            host_kind="codex",
            context=(JournalSlice(str(history)),),
            target=(JournalSlice(str(target)),),
        ),
        host_factory=create_host,
    )

    assert "tokens=15 turns=1 errors=0" in prompts[0]
    assert "tokens=1100" not in prompts[0]


async def test_run_one_session_retries_legacy_episode_draft(
    tmp_path: Path,
) -> None:
    day = "2026-07-09"
    legacy = json.dumps(
        {
            "diary": [
                {
                    "date": day,
                    "outcomes": ["完成关键实现"] * 4,
                    "decisions": ["确定后续方案"] * 3,
                    "corrections": ["原判断被证据纠正"] * 3,
                    "open_loops": ["下一步待真实验证"] * 3,
                }
            ]
        }
    )
    revised = json.dumps(
        {
            "diary": [
                {
                    "date": day,
                    "items": [
                        {
                            "kind": "outcome",
                            "summary": "完成关键实现并通过相关验证",
                            "detail": "",
                        }
                    ],
                }
            ]
        }
    )
    holder: dict[str, object] = {}

    class RevisingHost:
        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir
            self.prompts: list[str] = []

        async def send(self, prompt: str):
            self.prompts.append(prompt)
            if len(self.prompts) == 2:
                (self.workdir / "draft.json").write_text(revised, encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    def create_host(
        _session: SessionRecord,
        workdir: Path,
    ) -> RevisingHost:
        (workdir / "draft.json").write_text(legacy, encoding="utf-8")
        host = RevisingHost(workdir)
        holder["host"] = host
        return host

    draft = await run_one_session(
        session(),
        day,
        tmp_path / "memory",
        review_source=review_source(),
        host_factory=create_host,
    )

    host = holder["host"]
    assert isinstance(host, RevisingHost)
    assert len(host.prompts) == 2
    assert "legacy structured lists" in host.prompts[1]
    assert draft.diary[0].outcomes == ("完成关键实现并通过相关验证",)


async def test_run_one_session_retries_unknown_feedback_kind(
    tmp_path: Path,
) -> None:
    invalid = json.dumps(
        {
            "notes": [
                {
                    "title": "slice 实现要 TDD 先写钉核心行为的失败测试",
                    "kind": "feedback",
                    "verification": "event-data-supported",
                }
            ]
        }
    )
    revised = json.dumps(
        {
            "notes": [
                {
                    "title": "slice 实现要 TDD 先写钉核心行为的失败测试",
                    "kind": "procedure",
                    "verification": "event-data-supported",
                }
            ]
        }
    )
    prompts: list[str] = []

    class RevisingHost:
        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir

        async def send(self, prompt: str):
            prompts.append(prompt)
            if len(prompts) == 2:
                (self.workdir / "draft.json").write_text(revised, encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    def create_host(
        _session: SessionRecord,
        workdir: Path,
    ) -> RevisingHost:
        (workdir / "draft.json").write_text(invalid, encoding="utf-8")
        return RevisingHost(workdir)

    draft = await run_one_session(
        session(),
        "2026-07-22",
        tmp_path / "memory",
        review_source=review_source(),
        host_factory=create_host,
    )

    assert len(prompts) == 2
    assert "unknown kind 'feedback'" in prompts[1]
    assert draft.notes[0].kind == "procedure"


async def test_real_host_is_created_with_review_kind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict = {}

    class FakeCCHost:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.workdir = str(kwargs["workdir"])

        async def send(self, prompt: str):
            Path(self.workdir, "draft.json").write_text(
                VALID_DRAFT,
                encoding="utf-8",
            )
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr("trowel_py.cc_host.service.CCHost", FakeCCHost)

    await run_one_session(
        session(),
        "2026-07-09",
        tmp_path / "memory",
        review_source=review_source(),
    )
    # review 类型是阻止提炼 session 自递归进入队列的真实边界。
    assert captured.get("session_kind") == "review"


async def test_run_one_session_error_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            review_source=review_source(),
            host_factory=factory([ERROR], VALID_DRAFT),
        )


async def test_run_one_session_no_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            review_source=review_source(),
            host_factory=factory([FINISHED]),
        )


async def test_run_one_session_does_not_reuse_stale_draft(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    await run_one_session(
        session(),
        "2026-07-09",
        memory_root,
        review_source=review_source(),
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    with pytest.raises(DistillError, match="draft.json was not created"):
        await run_one_session(
            session(),
            "2026-07-09",
            memory_root,
            review_source=review_source(),
            host_factory=factory([FINISHED]),
        )


async def test_run_one_session_invalid_draft_raises(tmp_path: Path) -> None:
    bad = json.dumps({"notes": [{"title": "x", "verification": "bogus"}]})
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            review_source=review_source(),
            host_factory=factory([FINISHED], bad),
        )


async def test_run_one_session_malformed_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            review_source=review_source(),
            host_factory=factory([FINISHED], "{not valid json"),
        )
    with pytest.raises(DistillError):
        await run_one_session(
            session("s2"),
            "2026-07-09",
            tmp_path / "memory",
            review_source=review_source(),
            host_factory=factory(
                [FINISHED],
                json.dumps({"notes": [{"title": "x", "pain": "high"}]}),
            ),
        )
