"""slice-041 weekly tidy tests: build_tidy_plan (LLM→plan) + run_weekly_tidy."""
from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import build_tidy_plan, run_weekly_tidy


class FakeProvider:
    """Returns canned responses in order; records calls."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0) if self.responses else "{}"


def _note(root: Path, mid: str, title: str, *, created: str = "2026-07-08",
          updated: str = "2026-07-08", conflicts_with: tuple[str, ...] = (),
          content_hash: str = "h1", body: str = "body") -> str:
    return MemoryStore(root).write_note({
        "type": "note", "title": title, "verification": "verified",
        "memory_id": mid, "status": "active", "conflicts_with": list(conflicts_with),
        "content_hash": content_hash, "created": created, "updated": updated,
        "__body": body,
    })


# ---------- build_tidy_plan ----------


def test_build_tidy_plan_parses_operations(tmp_path: Path) -> None:
    _note(tmp_path, "mid-old", "Old", created="2026-06-01", updated="2026-06-01",
          conflicts_with=(), body="old conclusion")
    _note(tmp_path, "mid-new", "New", created="2026-07-08", updated="2026-07-08",
          conflicts_with=("mid-old",), body="new corrects old")
    provider = FakeProvider(json.dumps({"operations": [
        {"type": "supersede", "target": "mid-old", "by": "mid-new",
         "reason": "new corrects old"},
    ]}))
    plan = build_tidy_plan(tmp_path, "2026-W28", provider)
    assert len(plan.operations) == 1
    op = plan.operations[0]
    assert op.type == "supersede"
    assert op.target == "mid-old"
    assert op.by == "mid-new"
    assert plan.plan_id == "weekly-2026-W28"


def test_build_tidy_plan_no_week_notes_skips_llm(tmp_path: Path) -> None:
    # notes exist but none were created/updated in this week
    _note(tmp_path, "mid-a", "A", created="2026-06-01", updated="2026-06-01")
    provider = FakeProvider()
    plan = build_tidy_plan(tmp_path, "2026-W28", provider)
    assert plan.operations == ()
    assert provider.calls == []  # no LLM call when nothing to tidy


def test_build_tidy_plan_drops_invalid_ops(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", created="2026-07-08")
    provider = FakeProvider(json.dumps({"operations": [
        {"type": "bogus", "target": "mid-a", "reason": "bad type"},  # dropped
        {"type": "retire", "target": "", "reason": "no target"},  # dropped
        {"type": "retire", "target": "mid-a", "reason": "ok"},  # kept
    ]}))
    plan = build_tidy_plan(tmp_path, "2026-W28", provider)
    assert len(plan.operations) == 1
    assert plan.operations[0].type == "retire"


def test_build_tidy_plan_non_json_returns_empty(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", created="2026-07-08")
    provider = FakeProvider("not json at all")
    plan = build_tidy_plan(tmp_path, "2026-W28", provider)
    assert plan.operations == ()


def test_build_tidy_plan_drops_hallucinated_refs(tmp_path: Path) -> None:
    # LLM hallucinated a `by` / `canonical` not in the known memory_ids → drop
    # those ops, keep the valid ones (don't reject the whole plan).
    _note(tmp_path, "mid-a", "A", created="2026-07-08")
    _note(tmp_path, "mid-b", "B", created="2026-07-08")
    provider = FakeProvider(json.dumps({"operations": [
        {"type": "supersede", "target": "mid-a", "by": "mid-ghost",
         "reason": "by not in notes"},  # dropped
        {"type": "merge_sources", "target": "mid-a", "canonical": "mid-phantom",
         "reason": "canonical not in notes"},  # dropped
        {"type": "retire", "target": "mid-b", "reason": "ok"},  # kept
    ]}))
    plan = build_tidy_plan(tmp_path, "2026-W28", provider)
    assert len(plan.operations) == 1
    assert plan.operations[0].type == "retire"
    assert plan.operations[0].target == "mid-b"


# ---------- run_weekly_tidy ----------


def test_run_weekly_tidy_compresses_and_applies(tmp_path: Path) -> None:
    # a daily in W28 + a note supersede plan
    MemoryStore(tmp_path).write_diary({
        "type": "diary", "date": "2026-07-08", "layer": "day",
        "period": "2026-07-08", "__body": "day events",
    })
    _note(tmp_path, "mid-old", "Old", created="2026-06-01", updated="2026-06-01",
          conflicts_with=())
    _note(tmp_path, "mid-new", "New", created="2026-07-08", updated="2026-07-08",
          conflicts_with=("mid-old",))
    # provider gets two calls: compress_weekly then build_tidy_plan
    weekly_json = json.dumps({"weekly": "周记", "bypass": {}})
    plan_json = json.dumps({"operations": [
        {"type": "supersede", "target": "mid-old", "by": "mid-new",
         "reason": "new corrects old"},
    ]})
    provider = FakeProvider(weekly_json, plan_json)
    report = run_weekly_tidy(tmp_path, "2026-W28", provider)
    assert report["compress"]["weekly_written"]
    assert report["tidy"]["operations"] == 1
    # the supersede was applied
    notes = {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-old"].status == "superseded"
    assert notes["mid-old"].superseded_by == "mid-new"


def test_run_weekly_tidy_no_ops_skips_apply(tmp_path: Path) -> None:
    MemoryStore(tmp_path).write_diary({
        "type": "diary", "date": "2026-07-08", "layer": "day",
        "period": "2026-07-08", "__body": "day events",
    })
    _note(tmp_path, "mid-a", "A", created="2026-07-08")
    weekly_json = json.dumps({"weekly": "周记", "bypass": {}})
    plan_json = json.dumps({"operations": []})  # no ops
    provider = FakeProvider(weekly_json, plan_json)
    report = run_weekly_tidy(tmp_path, "2026-W28", provider)
    assert report["compress"]["weekly_written"]
    assert report["tidy"]["operations"] == 0
    assert report["tidy"]["applied"] == []
