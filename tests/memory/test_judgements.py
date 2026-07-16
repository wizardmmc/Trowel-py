"""tests for judgement IO + the C-6 memory_id hard-check (slice-053).

The judge agent's draft is parsed in judge.py (loose); this module owns the
strict JudgementReport dataclass + its on-disk round-trip under
``meta/judgements/<cc_session_id>.json`` + the pure function that drops
judgements whose memory_id is not a real note (C-6 — fabricated ids do not
pollute the metrics).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.judgements import (
    HitJudgement,
    JudgementReport,
    MissJudgement,
    drop_unknown_memory_ids,
    load_all_judgement_reports,
    load_judgement_report,
    save_judgement_report,
)


def _hit(
    memory_id: str = "note-a",
    *,
    used: bool = True,
    outcome: str = "helpful",
) -> HitJudgement:
    return HitJudgement(
        memory_id=memory_id,
        used=used,
        outcome=outcome,
        reason="模型引用了这条笔记",
        evidence="turn 3 改了方向",
    )


def _miss(memory_id: str = "note-b", attribution: str = "retrieval_miss") -> MissJudgement:
    return MissJudgement(
        memory_id=memory_id,
        attribution=attribution,
        reason="当时没搜到这条",
        evidence="会话里没有相关 search",
    )


def _report(
    cc_session_id: str = "sess-1",
    *,
    hits=(),
    recall_miss=(),
    summary: str = "用得还行",
) -> JudgementReport:
    return JudgementReport(
        cc_session_id=cc_session_id,
        hits=tuple(hits),
        recall_miss=tuple(recall_miss),
        summary=summary,
    )


# ---------- immutability ----------


def test_judgement_dataclasses_are_frozen() -> None:
    h = _hit()
    m = _miss()
    r = _report(hits=(h,), recall_miss=(m,))
    with pytest.raises(Exception):
        h.memory_id = "x"  # type: ignore[misc]
    with pytest.raises(Exception):
        m.attribution = "x"  # type: ignore[misc]
    with pytest.raises(Exception):
        r.cc_session_id = "x"  # type: ignore[misc]


# ---------- IO round-trip ----------


def test_save_load_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    report = _report(
        hits=(_hit(),),
        recall_miss=(_miss(),),
    )
    save_judgement_report(root, report)
    back = load_judgement_report(root, "sess-1")
    assert back == report


def test_save_load_preserves_empty_tuples(tmp_path: Path) -> None:
    # a session with hits but no miss (or vice versa) round-trips intact.
    root = tmp_path / "memory"
    report = _report(hits=(_hit(), _hit("note-c", outcome="unused")), recall_miss=())
    save_judgement_report(root, report)
    back = load_judgement_report(root, "sess-1")
    assert back is not None
    assert len(back.hits) == 2
    assert back.recall_miss == ()


def test_load_missing_returns_none(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    assert load_judgement_report(root, "nope") is None


def test_load_all_returns_every_report(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    save_judgement_report(root, _report("sess-1"))
    save_judgement_report(root, _report("sess-2", summary="另一个"))
    all_reports = load_all_judgement_reports(root)
    assert {r.cc_session_id for r in all_reports} == {"sess-1", "sess-2"}


def test_load_all_empty_when_absent(tmp_path: Path) -> None:
    assert load_all_judgement_reports(tmp_path / "memory") == []


def test_judgement_file_path_uses_cc_session_id(tmp_path: Path) -> None:
    # C-3: each judged session gets its own file keyed by its cc_session_id.
    root = tmp_path / "memory"
    save_judgement_report(root, _report("abc-123"))
    assert (root / "meta" / "judgements" / "abc-123.json").exists()


# ---------- C-6: drop fabricated memory_ids ----------


def test_drop_unknown_memory_ids_filters_hits_and_misses() -> None:
    # only note-a / note-b are real; note-fake is fabricated by the agent.
    known = frozenset({"note-a", "note-b"})
    report = _report(
        hits=(_hit("note-a"), _hit("note-fake", outcome="unused")),
        recall_miss=(_miss("note-b"), _miss("note-fake-2")),
    )
    cleaned = drop_unknown_memory_ids(report, known)
    assert {h.memory_id for h in cleaned.hits} == {"note-a"}
    assert {m.memory_id for m in cleaned.recall_miss} == {"note-b"}


def test_drop_unknown_keeps_summary_and_session() -> None:
    known = frozenset({"note-a"})
    report = _report(hits=(_hit("note-a"),), recall_miss=(), summary="原样")
    cleaned = drop_unknown_memory_ids(report, known)
    assert cleaned.cc_session_id == report.cc_session_id
    assert cleaned.summary == "原样"


def test_drop_unknown_all_fabricated_yields_empty() -> None:
    known = frozenset({"note-a"})
    report = _report(
        hits=(_hit("ghost-1"),),
        recall_miss=(_miss("ghost-2"),),
    )
    cleaned = drop_unknown_memory_ids(report, known)
    assert cleaned.hits == ()
    assert cleaned.recall_miss == ()
