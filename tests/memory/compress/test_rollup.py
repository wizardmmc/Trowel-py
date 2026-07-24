import json
from pathlib import Path

from trowel_py.memory.compress import (
    _parse_weekly_output,
    compress_monthly,
    compress_weekly,
)
from trowel_py.memory.store import MemoryStore

from .support import FakeProvider, daily


def test_compress_weekly_writes_weekly_and_bypass(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-06", "day A events with gotcha")
    daily(tmp_path, "2026-07-08", "day B events")
    provider = FakeProvider("## 周记\n本周主线...")
    report = compress_weekly(tmp_path, "2026-W28", provider)
    assert report["weekly_written"]
    weeklies = MemoryStore(tmp_path).load_diary(layer="week")
    assert any(weekly.period == "2026-W28" for weekly in weeklies)


def test_compress_weekly_no_dailies_returns_empty(tmp_path: Path) -> None:
    provider = FakeProvider()
    report = compress_weekly(tmp_path, "2026-W28", provider)
    assert report["weekly_written"] is False
    assert provider.calls == []


def test_compress_weekly_only_matches_iso_week(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-05", "W27_SUNDAY")
    daily(tmp_path, "2026-07-06", "W28_MONDAY")
    provider = FakeProvider("周记")
    compress_weekly(tmp_path, "2026-W28", provider)
    assert "W28_MONDAY" in provider.calls[0][1]
    assert "W27_SUNDAY" not in provider.calls[0][1]


def test_compress_weekly_writes_bypass_files(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-06", "tech detail + emotional scene")
    output = json.dumps(
        {
            "weekly": "周记正文",
            "bypass": {
                "technical-detail": "技术细节正文",
                "emotional-trigger": "情感场景正文",
                "cross-week-causal": "",
            },
        }
    )
    report = compress_weekly(tmp_path, "2026-W28", FakeProvider(output))
    assert report["weekly_written"]
    assert report["bypass"]["technical-detail"] is True
    assert report["bypass"]["emotional-trigger"] is True
    assert report["bypass"]["cross-week-causal"] is False
    bypass_root = tmp_path / "diary" / "bypass"
    assert (bypass_root / "technical-detail" / "2026-W28.md").exists()
    assert (bypass_root / "emotional-trigger" / "2026-W28.md").exists()
    assert not (bypass_root / "cross-week-causal" / "2026-W28.md").exists()
    [weekly] = [
        entry
        for entry in MemoryStore(tmp_path).load_diary(layer="week")
        if entry.period == "2026-W28"
    ]
    assert weekly.body == "周记正文"


def test_compress_weekly_non_json_falls_back_to_weekly_only(
    tmp_path: Path,
) -> None:
    daily(tmp_path, "2026-07-06", "events")
    report = compress_weekly(
        tmp_path,
        "2026-W28",
        FakeProvider("just weekly text, no JSON"),
    )
    assert report["weekly_written"]
    assert all(value is False for value in report["bypass"].values())
    [weekly] = [
        entry
        for entry in MemoryStore(tmp_path).load_diary(layer="week")
        if entry.period == "2026-W28"
    ]
    assert weekly.body == "just weekly text, no JSON"


def test_parse_weekly_empty_weekly_keeps_bypass_isolated() -> None:
    raw = (
        '{"weekly": "", "bypass": {"technical-detail": "td body", '
        '"emotional-trigger": "et body", "cross-week-causal": "cc body"}}'
    )
    output = _parse_weekly_output(raw)
    assert output["weekly"] == ""
    assert "td body" not in output["weekly"]
    assert output["bypass"]["technical-detail"] == "td body"
    assert output["bypass"]["emotional-trigger"] == "et body"
    assert output["bypass"]["cross-week-causal"] == "cc body"


def test_parse_weekly_non_json_uses_raw_fallback() -> None:
    output = _parse_weekly_output("just plain text, no json braces")
    assert output["weekly"] == "just plain text, no json braces"
    assert output["bypass"] == {}


def test_compress_weekly_caps_long_weekly(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-06", "day body")
    long_weekly = "周" * 1200
    raw = json.dumps({"weekly": long_weekly, "bypass": {}})
    compress_weekly(tmp_path, "2026-W28", FakeProvider(raw))
    [weekly] = [
        entry
        for entry in MemoryStore(tmp_path).load_diary(layer="week")
        if entry.period == "2026-W28"
    ]
    assert len(weekly.body) <= 800


def test_compress_monthly_caps_long_output(tmp_path: Path) -> None:
    MemoryStore(tmp_path).write_diary(
        {
            "type": "diary",
            "date": "2026-W28",
            "layer": "week",
            "period": "2026-W28",
            "promoted_knowledge": [],
            "__body": "weekly body",
        }
    )
    long_body = "月" * 1200
    compress_monthly(tmp_path, "2026-07", FakeProvider(long_body))
    [monthly] = [
        entry
        for entry in MemoryStore(tmp_path).load_diary(layer="month")
        if entry.period == "2026-07"
    ]
    assert len(monthly.body) <= 800
