from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.daily_review.support import FINISHED, session
from trowel_py.memory.review_job import run_one_session
from trowel_py.memory.sessions_repo import SessionRecord


def _valid_draft() -> str:
    return json.dumps(
        {
            "diary": [
                {
                    "date": "2026-07-09",
                    "items": [
                        {
                            "kind": "outcome",
                            "summary": "完成提炼",
                            "detail": "",
                        }
                    ],
                }
            ]
        }
    )


async def test_agent_uses_original_source_path_and_cc_byte_range(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    first = json.dumps({"type": "user", "text": "first"}) + "\n"
    second = json.dumps({"type": "assistant", "text": "second"}) + "\n"
    source.write_text(first + second, encoding="utf-8")
    record = SessionRecord(
        cc_session_id="s-raw",
        workdir="/project",
        date="2026-07-09",
        jsonl_path=str(source),
        registered_at="2026-07-09T10:00:00",
    )
    prompts: list[str] = []
    review_workdir = (
        tmp_path / "review-daily-work" / "2026-07-09" / record.cc_session_id
    )
    review_workdir.mkdir(parents=True)
    stale_numbered = review_workdir / "source-deadbeef.numbered.jsonl"
    stale_numbered.write_text("legacy generated copy", encoding="utf-8")

    class Host:
        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir

        async def send(self, prompt: str):
            prompts.append(prompt)
            (self.workdir / "draft.json").write_text(_valid_draft(), encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    await run_one_session(
        record,
        "2026-07-09",
        tmp_path / "memory",
        start_offset=len(first.encode()),
        end_offset=source.stat().st_size,
        host_factory=lambda _session, workdir: Host(workdir),
    )

    assert not stale_numbered.exists()
    assert list((tmp_path / "review-daily-work").rglob("*.numbered.jsonl")) == []
    assert str(source) in prompts[0]
    assert f"[{len(first.encode())}, {source.stat().st_size})" in prompts[0]
    assert "起点以前" in prompts[0]
    assert "终点以后" in prompts[0]


async def test_agent_accepts_draft_without_source_refs(tmp_path: Path) -> None:
    prompts: list[str] = []

    class Host:
        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir

        async def send(self, prompt: str):
            prompts.append(prompt)
            (self.workdir / "draft.json").write_text(_valid_draft(), encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    draft = await run_one_session(
        session(),
        "2026-07-09",
        tmp_path / "memory",
        host_factory=lambda _session, workdir: Host(workdir),
    )

    assert len(prompts) == 1
    assert not hasattr(draft.diary[0].items[0], "source_refs")


async def test_codex_agent_uses_sealed_original_journal_without_byte_range(
    tmp_path: Path,
) -> None:
    source = tmp_path / "turn.jsonl"
    source.write_text('{"type":"user"}\n', encoding="utf-8")
    record = SessionRecord(
        cc_session_id="codex-thread",
        workdir="/project",
        date="2026-07-09",
        jsonl_path=str(source),
        registered_at="2026-07-09T10:00:00",
    )
    prompts: list[str] = []

    class Host:
        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir

        async def send(self, prompt: str):
            prompts.append(prompt)
            (self.workdir / "draft.json").write_text(_valid_draft(), encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    await run_one_session(
        record,
        "2026-07-09",
        tmp_path / "memory",
        source_runtime="codex",
        host_factory=lambda _session, workdir: Host(workdir),
    )

    assert str(source) in prompts[0]
    assert "【来源范围】" not in prompts[0]
    assert "一个或多个已完成 Codex turn" in prompts[0]
    assert list((tmp_path / "review-daily-work").rglob("*.numbered.jsonl")) == []


@pytest.mark.parametrize("source_runtime", ["claude_code", "codex"])
async def test_production_distill_host_explicitly_uses_glm_5_1(
    source_runtime: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeCCHost:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.workdir = Path(str(kwargs["workdir"]))

        async def send(self, _prompt: str):
            (self.workdir / "draft.json").write_text(_valid_draft(), encoding="utf-8")
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr("trowel_py.cc_host.service.CCHost", FakeCCHost)

    await run_one_session(
        session(),
        "2026-07-09",
        tmp_path / "memory",
        source_runtime=source_runtime,
    )

    assert captured["model"] == "glm-5.1"
