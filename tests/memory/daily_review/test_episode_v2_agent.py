from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.daily_review.support import FINISHED, session
from trowel_py.memory.review_job import run_one_session
from trowel_py.memory.sessions_repo import SessionRecord


def _valid_draft(ref: str = "L000001") -> str:
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
                            "source_refs": [ref],
                        }
                    ],
                }
            ]
        }
    )


async def test_agent_materializes_exact_numbered_segment_and_validates_refs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    first = json.dumps({"type": "user", "text": "first"}) + "\n"
    second = json.dumps({"type": "assistant", "text": "second"}) + "\n"
    source.write_text(first + second, encoding="utf-8")
    record = SessionRecord(
        cc_session_id="s-numbered",
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
            (self.workdir / "draft.json").write_text(
                _valid_draft(), encoding="utf-8"
            )
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

    [numbered] = list((tmp_path / "review-daily-work").rglob("source-*.numbered.jsonl"))
    numbered_text = numbered.read_text(encoding="utf-8")
    assert numbered_text.startswith("L000001\t")
    assert "second" in numbered_text
    assert "first" not in numbered_text
    assert str(numbered) in prompts[0]


async def test_agent_revises_illegal_source_ref(tmp_path: Path) -> None:
    prompts: list[str] = []

    class Host:
        def __init__(self, workdir: Path) -> None:
            self.workdir = workdir

        async def send(self, prompt: str):
            prompts.append(prompt)
            ref = "L999999" if len(prompts) == 1 else "L000001"
            (self.workdir / "draft.json").write_text(
                _valid_draft(ref), encoding="utf-8"
            )
            yield FINISHED

        async def close(self) -> None:
            pass

    draft = await run_one_session(
        session(),
        "2026-07-09",
        tmp_path / "memory",
        host_factory=lambda _session, workdir: Host(workdir),
    )

    assert len(prompts) == 2
    assert "illegal refs" in prompts[1]
    assert draft.diary[0].items[0].source_refs == ("L000001",)


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
            (self.workdir / "draft.json").write_text(
                _valid_draft(), encoding="utf-8"
            )
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
