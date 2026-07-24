"""judge agent 生命周期与失败隔离。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.judge.support import (
    ERROR,
    FINISHED,
    _VALID_DRAFT,
    _factory,
    _seed_real_notes,
    _session,
)
from trowel_py.memory.judge import judge_session
from trowel_py.memory.judgements import (
    HitJudgement,
    JudgementReport,
    MissJudgement,
    load_judgement_report,
)


async def test_judge_session_parses_draft_and_saves(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _seed_real_notes(root, ("real-note", "real-note-2"))
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert report is not None
    assert isinstance(report, JudgementReport)
    assert report.cc_session_id == "judged-1"
    assert len(report.hits) == 1
    h = report.hits[0]
    assert isinstance(h, HitJudgement)
    assert h.used is True
    assert h.outcome == "helpful"
    assert h.reason
    assert h.evidence
    assert len(report.recall_miss) == 1
    assert isinstance(report.recall_miss[0], MissJudgement)
    assert report.recall_miss[0].attribution == "retrieval_miss"
    assert load_judgement_report(root, "judged-1") == report


async def test_judge_session_drops_fabricated_memory_ids(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _seed_real_notes(root, ("real-note",))
    draft = json.dumps(
        {
            "hits": [
                {
                    "memory_id": "real-note",
                    "used": True,
                    "outcome": "helpful",
                    "reason": "r",
                    "evidence": "e",
                },
                {
                    "memory_id": "ghost",
                    "used": False,
                    "outcome": "unused",
                    "reason": "r",
                    "evidence": "e",
                },
            ],
            "recall_miss": [
                {
                    "memory_id": "ghost-2",
                    "attribution": "awareness_miss",
                    "reason": "r",
                    "evidence": "e",
                },
            ],
            "summary": "x",
        }
    )
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], draft),
    )
    assert report is not None
    assert {h.memory_id for h in report.hits} == {"real-note"}
    assert report.recall_miss == ()


async def test_judge_session_returns_none_on_error_event(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([ERROR], _VALID_DRAFT),
    )
    assert report is None


async def test_judge_session_returns_none_on_no_draft(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], draft_text=None),
    )
    assert report is None


async def test_judge_session_returns_none_on_bad_json(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], "{not valid json"),
    )
    assert report is None


async def test_judge_session_closes_host_when_send_raises(tmp_path: Path) -> None:
    closed = False

    class FailingHost:
        async def send(self, prompt: str):
            if False:
                yield FINISHED
            raise RuntimeError("send failed")

        async def close(self) -> None:
            nonlocal closed
            closed = True

    report = await judge_session(
        _session(),
        "2026-07-16",
        tmp_path / "memory",
        host_factory=lambda session, workdir: FailingHost(),
    )

    assert report is None
    assert closed is True


async def test_judge_session_cchost_eval_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}
    root = tmp_path / "memory"

    class FakeCCHost:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self._workdir = str(kwargs.get("workdir"))

        async def send(self, prompt: str):
            Path(self._workdir, "judgement-draft.json").write_text(
                _VALID_DRAFT, encoding="utf-8"
            )
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr("trowel_py.cc_host.service.CCHost", FakeCCHost)
    report = await judge_session(_session(), "2026-07-16", root)
    assert report is not None
    assert captured["session_kind"] == "eval"
    assert captured.get("mcp_config")


async def test_judge_session_no_access_log_still_works(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _seed_real_notes(root, ("real-note", "real-note-2"))
    report = await judge_session(
        _session(),
        "2026-07-16",
        root,
        host_factory=_factory([FINISHED], _VALID_DRAFT),
    )
    assert report is not None
