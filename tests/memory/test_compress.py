"""slice-041 diary compression tests (daily/weekly/monthly + bypass).

Each layer caps at ~800 chars (grill 2026-07-11). Span越大越流水: daily keeps
gotcha/pain/decision; weekly keeps event-stream + bypass refs; monthly is
flow-only. Bypass files are week-level only (S3 — never enter monthly).
"""
from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.compress import (
    _parse_weekly_output,
    compress_daily,
    compress_monthly,
    compress_weekly,
)
from trowel_py.memory.store import MemoryStore, _dump_frontmatter


class FakeProvider:
    """Minimal LLMProvider: returns a canned response, records calls."""

    def __init__(self, response: str = "压缩版") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.response


def _episode(root: Path, sid: str, date: str, body: str,
             registered_at: str = "2026-07-01T10:00:00") -> Path:
    path = root / "episodes" / f"{sid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "type": "episode", "cc_session_id": sid, "workdir": "/tmp",
        "registered_at": registered_at, "review_date": date,
        "activity_dates": [date], "source_jsonl": "/tmp/x.jsonl", "segments": [],
    }
    path.write_text(_dump_frontmatter(fm, body), encoding="utf-8")
    return path


def _daily(root: Path, date: str, body: str) -> None:
    MemoryStore(root).write_diary({
        "type": "diary", "date": date, "layer": "day", "period": date,
        "promoted_knowledge": [], "__body": body,
    })


# ---------- daily ----------


def test_compress_daily_writes_compressed(tmp_path: Path) -> None:
    _episode(tmp_path, "s1", "2026-07-01", "## 2026-07-01\n\nlong raw events")
    compress_daily(tmp_path, "2026-07-01", FakeProvider("压缩版日记"))
    [d] = MemoryStore(tmp_path).load_diary(layer="day")
    assert d.body == "压缩版日记"


def test_compress_daily_no_episodes_returns_empty(tmp_path: Path) -> None:
    provider = FakeProvider()
    assert compress_daily(tmp_path, "2026-07-01", provider) == ""
    assert provider.calls == []  # no LLM call when nothing to compress


def test_compress_daily_passes_raw_to_provider(tmp_path: Path) -> None:
    _episode(tmp_path, "s1", "2026-07-01", "RAW_EVENTS_BODY")
    provider = FakeProvider("压缩")
    compress_daily(tmp_path, "2026-07-01", provider)
    assert provider.calls
    assert "RAW_EVENTS_BODY" in provider.calls[0][1]


def test_compress_daily_overwrites_same_date(tmp_path: Path) -> None:
    _episode(tmp_path, "s1", "2026-07-01", "events")
    _daily(tmp_path, "2026-07-01", "old aggregate")
    compress_daily(tmp_path, "2026-07-01", FakeProvider("new compressed"))
    [d] = MemoryStore(tmp_path).load_diary(layer="day")
    assert d.body == "new compressed"


def test_compress_daily_only_matches_reviews_date(tmp_path: Path) -> None:
    # an episode for a different review_date must not feed this day's compress
    _episode(tmp_path, "s1", "2026-07-02", "OTHER_DAY")
    _episode(tmp_path, "s2", "2026-07-01", "THIS_DAY")
    provider = FakeProvider("压缩")
    compress_daily(tmp_path, "2026-07-01", provider)
    assert "THIS_DAY" in provider.calls[0][1]
    assert "OTHER_DAY" not in provider.calls[0][1]


# ---------- weekly ----------


def test_compress_weekly_writes_weekly_and_bypass(tmp_path: Path) -> None:
    # two dailies in the same ISO week (2026-W28: 2026-07-06..07-12)
    _daily(tmp_path, "2026-07-06", "day A events with gotcha")
    _daily(tmp_path, "2026-07-08", "day B events")
    provider = FakeProvider("## 周记\n本周主线...")
    report = compress_weekly(tmp_path, "2026-W28", provider)
    assert report["weekly_written"]
    weeklies = MemoryStore(tmp_path).load_diary(layer="week")
    assert any(w.period == "2026-W28" for w in weeklies)


def test_compress_weekly_no_dailies_returns_empty(tmp_path: Path) -> None:
    provider = FakeProvider()
    report = compress_weekly(tmp_path, "2026-W28", provider)
    assert report["weekly_written"] is False
    assert provider.calls == []


def test_compress_weekly_only_matches_iso_week(tmp_path: Path) -> None:
    # 2026-07-05 is Sunday of W27; 2026-07-06 is Monday of W28
    _daily(tmp_path, "2026-07-05", "W27_SUNDAY")
    _daily(tmp_path, "2026-07-06", "W28_MONDAY")
    provider = FakeProvider("周记")
    compress_weekly(tmp_path, "2026-W28", provider)
    assert "W28_MONDAY" in provider.calls[0][1]
    assert "W27_SUNDAY" not in provider.calls[0][1]


def test_compress_weekly_writes_bypass_files(tmp_path: Path) -> None:
    # C-2: three bypass categories land in diary/bypass/<cat>/<iso_week>.md;
    # empty categories produce no file.
    _daily(tmp_path, "2026-07-06", "tech detail + emotional scene")
    out = json.dumps({"weekly": "周记正文", "bypass": {
        "technical-detail": "技术细节正文",
        "emotional-trigger": "情感场景正文",
        "cross-week-causal": "",  # empty → no file
    }})
    report = compress_weekly(tmp_path, "2026-W28", FakeProvider(out))
    assert report["weekly_written"]
    assert report["bypass"]["technical-detail"] is True
    assert report["bypass"]["emotional-trigger"] is True
    assert report["bypass"]["cross-week-causal"] is False
    bp = tmp_path / "diary" / "bypass"
    assert (bp / "technical-detail" / "2026-W28.md").exists()
    assert (bp / "emotional-trigger" / "2026-W28.md").exists()
    assert not (bp / "cross-week-causal" / "2026-W28.md").exists()
    [w] = [d for d in MemoryStore(tmp_path).load_diary(layer="week")
           if d.period == "2026-W28"]
    assert w.body == "周记正文"


def test_compress_weekly_non_json_falls_back_to_weekly_only(tmp_path: Path) -> None:
    # a non-JSON LLM response degrades to weekly-only (raw text as weekly body)
    _daily(tmp_path, "2026-07-06", "events")
    report = compress_weekly(tmp_path, "2026-W28", FakeProvider("just weekly text, no JSON"))
    assert report["weekly_written"]
    assert all(v is False for v in report["bypass"].values())
    [w] = [d for d in MemoryStore(tmp_path).load_diary(layer="week")
           if d.period == "2026-W28"]
    assert w.body == "just weekly text, no JSON"


def test_parse_weekly_empty_weekly_keeps_bypass_isolated() -> None:
    """W5 (codex): valid JSON with empty weekly must NOT fall back to raw —
    that would leak bypass bodies into the weekly diary (C-2 isolation)."""
    raw = (
        '{"weekly": "", "bypass": {"technical-detail": "td body", '
        '"emotional-trigger": "et body", "cross-week-causal": "cc body"}}'
    )
    out = _parse_weekly_output(raw)
    assert out["weekly"] == ""  # not the raw JSON
    assert "td body" not in out["weekly"]  # bypass not leaked into weekly
    assert out["bypass"]["technical-detail"] == "td body"
    assert out["bypass"]["emotional-trigger"] == "et body"
    assert out["bypass"]["cross-week-causal"] == "cc body"


def test_parse_weekly_non_json_uses_raw_fallback() -> None:
    """W5: only when the response is not JSON at all do we use raw as weekly."""
    out = _parse_weekly_output("just plain text, no json braces")
    assert out["weekly"] == "just plain text, no json braces"
    assert out["bypass"] == {}


# ---------- W4 (codex): hard 800-char cap on persist ----------


def test_compress_daily_caps_long_output(tmp_path: Path) -> None:
    """W4: daily output is hard-capped on persist (prompt cap was soft)."""
    _episode(tmp_path, "s1", "2026-07-01", "raw events")
    long_body = "字" * 1200
    compress_daily(tmp_path, "2026-07-01", FakeProvider(long_body))
    [d] = MemoryStore(tmp_path).load_diary(layer="day")
    assert len(d.body) <= 800
    assert d.body.endswith("…")


def test_compress_weekly_caps_long_weekly(tmp_path: Path) -> None:
    """W4: weekly body is hard-capped on persist."""
    _daily(tmp_path, "2026-07-06", "day body")
    long_weekly = "周" * 1200
    raw = json.dumps({"weekly": long_weekly, "bypass": {}})
    compress_weekly(tmp_path, "2026-W28", FakeProvider(raw))
    [w] = [d for d in MemoryStore(tmp_path).load_diary(layer="week")
           if d.period == "2026-W28"]
    assert len(w.body) <= 800


def test_compress_monthly_caps_long_output(tmp_path: Path) -> None:
    """W4: monthly body is hard-capped on persist."""
    MemoryStore(tmp_path).write_diary({
        "type": "diary", "date": "2026-W28", "layer": "week",
        "period": "2026-W28", "promoted_knowledge": [], "__body": "weekly body",
    })
    long_body = "月" * 1200
    compress_monthly(tmp_path, "2026-07", FakeProvider(long_body))
    [m] = [d for d in MemoryStore(tmp_path).load_diary(layer="month")
           if d.period == "2026-07"]
    assert len(m.body) <= 800
