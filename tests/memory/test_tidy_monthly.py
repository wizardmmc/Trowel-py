"""slice-041 monthly tidy tests: retire (90-day half-life + harmful) +
promote (helpful≥30) + compress_monthly + run_monthly_tidy."""
from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.compress import compress_monthly
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import (
    plan_retirements,
    promote_candidates,
    run_monthly_tidy,
)


class FakeProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0) if self.responses else "{}"


def _note(root: Path, mid: str, title: str, *, kind: str = "fact",
          status: str = "active", last_ref: str = "", helpful_refs: int = 0,
          harmful_refs: int = 0, memory_id: str | None = None,
          content_hash: str = "h1", body: str = "body",
          created: str = "", updated: str = "",
          conflicts_with: tuple[str, ...] = ()) -> str:
    entry = {
        "type": "note", "title": title, "verification": "verified",
        "kind": kind, "memory_id": memory_id or mid, "status": status,
        "last_ref": last_ref, "helpful_refs": helpful_refs,
        "harmful_refs": harmful_refs, "content_hash": content_hash,
        "conflicts_with": list(conflicts_with), "__body": body,
    }
    if created:
        entry["created"] = created
    if updated:
        entry["updated"] = updated
    return MemoryStore(root).write_note(entry)


# ---------- plan_retirements ----------


def test_retire_90_day_unused(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", last_ref="2026-04-01")  # >90 days before 07-11
    ops = plan_retirements(tmp_path, "2026-07-11")
    assert len(ops) == 1
    assert ops[0].type == "retire"
    assert ops[0].target == "mid-a"


def test_retire_last_ref_empty_protected(tmp_path: Path) -> None:
    # legacy note with no last_ref → NOT retired (protects pre-040-c notes)
    _note(tmp_path, "mid-a", "A", last_ref="")
    ops = plan_retirements(tmp_path, "2026-07-11")
    assert ops == ()


def test_retire_recent_last_ref_kept(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", last_ref="2026-07-01")  # 10 days ago
    ops = plan_retirements(tmp_path, "2026-07-11")
    assert ops == ()


def test_retire_high_harmful_even_without_last_ref(tmp_path: Path) -> None:
    # C-8: harmful_refs≥3 → retire even with no last_ref (doesn't wait half-life)
    _note(tmp_path, "mid-a", "A", last_ref="", harmful_refs=5)
    ops = plan_retirements(tmp_path, "2026-07-11")
    assert len(ops) == 1
    assert "harmful_refs=5" in ops[0].reason


def test_retire_skips_non_active(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", status="retired", last_ref="2024-01-01")
    _note(tmp_path, "mid-b", "B", status="superseded", last_ref="2024-01-01")
    ops = plan_retirements(tmp_path, "2026-07-11")
    assert ops == ()


# ---------- promote_candidates ----------


def test_promote_helpful_gotcha(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", kind="gotcha", helpful_refs=35)
    promoted = promote_candidates(tmp_path)
    assert "mid-a" in promoted
    assert (tmp_path / "meta" / "core-candidates" / "mid-a.md").exists()


def test_promote_below_threshold_skipped(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", kind="gotcha", helpful_refs=10)
    assert promote_candidates(tmp_path) == []
    assert not (tmp_path / "meta" / "core-candidates" / "mid-a.md").exists()


def test_promote_non_gotcha_skipped(tmp_path: Path) -> None:
    # only gotcha notes promote (procedural trigger for layer-one)
    _note(tmp_path, "mid-a", "A", kind="fact", helpful_refs=50)
    assert promote_candidates(tmp_path) == []


def test_promote_does_not_touch_core_md(tmp_path: Path) -> None:
    # C-11: monthly promote NEVER writes core.md
    _note(tmp_path, "mid-a", "A", kind="gotcha", helpful_refs=40)
    promote_candidates(tmp_path)
    assert not (tmp_path / "core.md").exists()


# ---------- compress_monthly ----------


def test_compress_monthly_writes_monthly(tmp_path: Path) -> None:
    MemoryStore(tmp_path).write_diary({
        "type": "diary", "date": "2026-W27", "layer": "week",
        "period": "2026-W27", "__body": "week 27",
    })
    MemoryStore(tmp_path).write_diary({
        "type": "diary", "date": "2026-W28", "layer": "week",
        "period": "2026-W28", "__body": "week 28",
    })
    report = compress_monthly(tmp_path, "2026-07", FakeProvider("月记正文"))
    assert report["monthly_written"]
    monthlies = MemoryStore(tmp_path).load_diary(layer="month")
    assert any(m.period == "2026-07" for m in monthlies)


def test_compress_monthly_no_weeklies(tmp_path: Path) -> None:
    provider = FakeProvider()
    report = compress_monthly(tmp_path, "2026-07", provider)
    assert report["monthly_written"] is False
    assert provider.calls == []


# ---------- run_monthly_tidy ----------


def test_run_monthly_tidy_end_to_end(tmp_path: Path) -> None:
    # a stale note to retire + a weekly to compress + a note to supersede
    _note(tmp_path, "mid-stale", "Stale", last_ref="2026-04-01",
          content_hash="hs")
    _note(tmp_path, "mid-old", "Old", created="2026-07-08", updated="2026-07-08",
          conflicts_with=(), content_hash="ho")
    _note(tmp_path, "mid-new", "New", created="2026-07-08", updated="2026-07-08",
          conflicts_with=("mid-old",), content_hash="hn")
    MemoryStore(tmp_path).write_diary({
        "type": "diary", "date": "2026-W28", "layer": "week",
        "period": "2026-W28", "__body": "week 28 events",
    })
    # provider: compress_monthly then build_monthly_plan
    provider = FakeProvider(
        "月记正文",
        json.dumps({"operations": [
            {"type": "supersede", "target": "mid-old", "by": "mid-new",
             "reason": "new corrects old"},
        ]}),
    )
    report = run_monthly_tidy(tmp_path, "2026-07", provider, today="2026-07-11")
    assert report["compress"]["monthly_written"]
    assert report["retire_ops"] == 1  # mid-stale retired
    # the supersede was applied
    notes = {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-stale"].status == "retired"
    assert notes["mid-old"].status == "superseded"
    assert notes["mid-old"].superseded_by == "mid-new"
