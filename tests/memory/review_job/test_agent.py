from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.review_job.support import (
    ERROR,
    FINISHED,
    VALID_DRAFT,
    factory,
    session,
)
from trowel_py.memory.review_job import DistillError, run_one_session
from trowel_py.memory.sessions_repo import SessionRecord


async def test_run_one_session_reads_draft(tmp_path: Path) -> None:
    draft = await run_one_session(
        session(),
        "2026-07-09",
        tmp_path / "memory",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )
    assert len(draft.notes) == 1
    assert draft.notes[0].verification == "verified"


async def test_run_one_session_retries_oversized_episode_draft(
    tmp_path: Path,
) -> None:
    day = "2026-07-09"
    oversized = json.dumps(
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
                    "outcomes": ["完成关键实现并通过相关验证"],
                    "decisions": ["确定后续采用可追溯方案"],
                    "corrections": ["原判断被真实证据纠正"],
                    "open_loops": ["下一步补真实端到端验证"],
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
        (workdir / "draft.json").write_text(oversized, encoding="utf-8")
        host = RevisingHost(workdir)
        holder["host"] = host
        return host

    draft = await run_one_session(
        session(),
        day,
        tmp_path / "memory",
        host_factory=create_host,
    )

    host = holder["host"]
    assert isinstance(host, RevisingHost)
    assert len(host.prompts) == 2
    assert "too many structured items" in host.prompts[1]
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

    await run_one_session(session(), "2026-07-09", tmp_path / "memory")
    # review 类型是阻止提炼 session 自递归进入队列的真实边界。
    assert captured.get("session_kind") == "review"


async def test_run_one_session_error_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=factory([ERROR], VALID_DRAFT),
        )


async def test_run_one_session_no_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=factory([FINISHED]),
        )


async def test_run_one_session_invalid_draft_raises(tmp_path: Path) -> None:
    bad = json.dumps({"notes": [{"title": "x", "verification": "bogus"}]})
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=factory([FINISHED], bad),
        )


async def test_run_one_session_malformed_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session(),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=factory([FINISHED], "{not valid json"),
        )
    with pytest.raises(DistillError):
        await run_one_session(
            session("s2"),
            "2026-07-09",
            tmp_path / "memory",
            host_factory=factory(
                [FINISHED],
                json.dumps({"notes": [{"title": "x", "pain": "high"}]}),
            ),
        )
