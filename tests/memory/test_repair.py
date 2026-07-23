"""tests for slice-040-a historical repair (backfill episodes from drafts).

Fixture sessions mirror the real 07-09 shape (cc_session_id / workdir /
jsonl_path from sessions.db; drafts under review-daily-work/<date>/<sid>/).
All in tmp_path — repair NEVER touches ``~/.trowel/memory`` from the test suite.
"""
from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.repair import repair_memory
from trowel_py.memory.daily_review.workspace import review_workdir_root
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore

_DATE = "2026-07-09"


def _register(mem: Path, sid: str, workdir: str = "/proj", at: str = "2026-07-09T10:00:00") -> None:
    conn = open_sessions_db(mem)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id=sid,
                workdir=workdir,
                date=_DATE,
                jsonl_path=f"/jsonl/{sid}.jsonl",
                registered_at=at,
            )
        )
    finally:
        conn.close()


def _place_draft(mem: Path, sid: str, events: str, date: str = _DATE) -> None:
    dp = review_workdir_root(mem) / _DATE / sid / "draft.json"
    dp.parent.mkdir(parents=True, exist_ok=True)
    dp.write_text(
        json.dumps(
            {
                "notes": [{"title": f"结论 {sid}", "verification": "verified"}],
                "diary": [{"date": date, "events": events}],
            }
        ),
        encoding="utf-8",
    )


# ---------- dry-run ----------


def test_dry_run_lists_plans_without_writing(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    _register(mem, "s1")
    _register(mem, "s2")
    _place_draft(mem, "s1", "s1 经历")
    _place_draft(mem, "s2", "s2 经历")

    report = repair_memory(mem, _DATE, apply=False)
    assert not report.applied
    assert len(report.planned) == 2
    assert {p.cc_session_id for p in report.planned} == {"s1", "s2"}
    assert all(p.has_draft for p in report.planned)
    # dry-run writes nothing
    assert not (mem / "episodes").exists()
    assert report.backup_dir is None


def test_dry_run_flags_missing_drafts(tmp_path: Path) -> None:
    # a session registered but with no surviving draft → listed as missing.
    mem = tmp_path / "memory"
    _register(mem, "s1")
    _register(mem, "s2-no-draft")
    _place_draft(mem, "s1", "s1 经历")

    report = repair_memory(mem, _DATE, apply=False)
    assert report.missing_drafts == ("s2-no-draft",)


def test_dry_run_flags_draft_without_session_record(tmp_path: Path) -> None:
    # a draft whose sid isn't in sessions.db → has_session_record=False (repair
    # still backfills it, with empty provenance — draft survival is enough).
    mem = tmp_path / "memory"
    _place_draft(mem, "orphan-sid", "孤儿经历")

    report = repair_memory(mem, _DATE, apply=False)
    [plan] = report.planned
    assert plan.cc_session_id == "orphan-sid"
    assert plan.has_draft
    assert not plan.has_session_record


# ---------- apply ----------


def test_apply_creates_one_episode_per_draft(tmp_path: Path) -> None:
    # spec 通过标准: 07-09 的 N 个 draft 回填为 N 个 episode。
    mem = tmp_path / "memory"
    for i, sid in enumerate(["s1", "s2", "s3"]):
        _register(mem, sid, workdir=f"/proj{i}", at=f"2026-07-09T1{i}:00:00")
        _place_draft(mem, sid, f"session {sid} 独有经历")

    report = repair_memory(mem, _DATE, apply=True)
    assert report.applied
    assert report.episodes_created == 3
    assert report.ok
    eps = sorted((mem / "episodes").glob("*.md"))
    assert [p.stem for p in eps] == ["s1", "s2", "s3"]


def test_apply_rebuilds_daily_with_all_anchors(tmp_path: Path) -> None:
    # spec 通过标准: daily 可见全部 session 锚点（覆盖 bug 只剩 1 个）。
    mem = tmp_path / "memory"
    for i, sid in enumerate(["s1", "s2", "s3"]):
        _register(mem, sid, at=f"2026-07-09T1{i}:00:00")
        _place_draft(mem, sid, f"锚点 {sid}")

    repair_memory(mem, _DATE, apply=True)
    [d] = MemoryStore(mem).load_diary(layer="day")
    assert "锚点 s1" in d.body
    assert "锚点 s2" in d.body
    assert "锚点 s3" in d.body


def test_apply_backs_up_memory_root(tmp_path: Path) -> None:
    # 铁律: 动 memory 前先备份。
    mem = tmp_path / "memory"
    (mem / "notes").mkdir(parents=True)
    (mem / "notes" / "existing.md").write_text(
        "---\ntype: note\ntitle: 老笔记\nverification: verified\n---\n正文\n",
        encoding="utf-8",
    )
    _register(mem, "s1")
    _place_draft(mem, "s1", "经历")

    report = repair_memory(mem, _DATE, apply=True)
    assert report.backup_dir is not None
    backup = Path(report.backup_dir)
    assert backup.exists()
    # the backup carries the pre-repair state (the existing note)
    assert (backup / "notes" / "existing.md").exists()


def test_apply_does_not_touch_notes(tmp_path: Path) -> None:
    # repair only restores the experience track; notes already landed in 040.
    mem = tmp_path / "memory"
    (mem / "notes").mkdir(parents=True)
    (mem / "notes" / "existing.md").write_text(
        "---\ntype: note\ntitle: 老笔记\nverification: verified\n---\n正文\n",
        encoding="utf-8",
    )
    _register(mem, "s1")
    _place_draft(mem, "s1", "经历")

    before = (mem / "notes" / "existing.md").read_text(encoding="utf-8")
    repair_memory(mem, _DATE, apply=True)
    after = (mem / "notes" / "existing.md").read_text(encoding="utf-8")
    assert before == after
    # no NEW notes created from the draft (draft had a note, but repair ignores it)
    assert len(list((mem / "notes").glob("*.md"))) == 1


def test_apply_does_not_rerun_agent(tmp_path: Path) -> None:
    # repair reads drafts only — no cc host, no LLM. A corrupted draft is
    # skipped (counted as missing), not re-distilled.
    mem = tmp_path / "memory"
    _register(mem, "s1")
    dp = review_workdir_root(mem) / _DATE / "s1" / "draft.json"
    dp.parent.mkdir(parents=True, exist_ok=True)
    dp.write_text("{not valid json", encoding="utf-8")

    report = repair_memory(mem, _DATE, apply=True)
    assert report.episodes_created == 0  # corrupt draft skipped, not re-run


def test_apply_skips_wrong_shaped_draft(tmp_path: Path) -> None:
    # CRITICAL fix: valid JSON but wrong shape (notes not a list) must not crash
    # repair — it's skipped like any unreadable draft.
    mem = tmp_path / "memory"
    _register(mem, "s1")
    _register(mem, "s2")
    _place_draft(mem, "s2", "s2 经历")
    dp = review_workdir_root(mem) / _DATE / "s1" / "draft.json"
    dp.parent.mkdir(parents=True, exist_ok=True)
    dp.write_text(json.dumps({"notes": "not a list", "diary": []}), encoding="utf-8")

    report = repair_memory(mem, _DATE, apply=True)
    # s1 skipped (wrong shape), s2 landed
    assert report.episodes_created == 1
    assert (mem / "episodes" / "s2.md").exists()
    assert not (mem / "episodes" / "s1.md").exists()


def test_apply_same_second_rerun_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    # CRITICAL fix: a same-second re-run must not crash on backup FileExistsError.
    from datetime import datetime

    fixed = datetime(2026, 7, 11, 10, 0, 0)
    monkeypatch.setattr(
        "trowel_py.memory.repair.datetime",
        type(
            "DT",
            (),
            {
                "now": staticmethod(lambda *a, **k: fixed),
                "strftime": datetime.strftime,
            },
        ),
    )
    mem = tmp_path / "memory"
    _register(mem, "s1")
    _place_draft(mem, "s1", "经历")

    r1 = repair_memory(mem, _DATE, apply=True)
    r2 = repair_memory(mem, _DATE, apply=True)  # same timestamp → uniqued
    assert r1.ok and r2.ok
    assert r1.backup_dir != r2.backup_dir  # distinct snapshots
