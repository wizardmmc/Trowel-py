"""JSONL 字节片段的活动日期归因测试。"""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path

from trowel_py.memory.activity_dates import extract_activity_dates

CST = timezone(timedelta(hours=8))


def _line(**over: object) -> bytes:
    obj = {"type": "user", "timestamp": "2026-07-16T10:00:00.000Z", **over}
    return (json.dumps(obj) + "\n").encode("utf-8")


def test_single_day(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(_line(timestamp="2026-07-16T02:00:00Z"))
    data = p.read_bytes()
    r = extract_activity_dates(p, 0, len(data), local_tz=CST)
    assert r.dates == ("2026-07-16",)
    assert r.basis == "jsonl_timestamp"


def test_cross_midnight_two_days(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(
        _line(timestamp="2026-07-16T15:00:00Z")
        + _line(timestamp="2026-07-16T16:00:00Z")
    )
    data = p.read_bytes()
    r = extract_activity_dates(p, 0, len(data), local_tz=CST)
    assert r.dates == ("2026-07-16", "2026-07-17")


def test_byte_range_slice(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    line1 = _line(timestamp="2026-07-15T02:00:00Z")
    line2 = _line(timestamp="2026-07-16T02:00:00Z")
    line3 = _line(timestamp="2026-07-17T02:00:00Z")
    p.write_bytes(line1 + line2 + line3)
    r = extract_activity_dates(p, len(line1), len(line1) + len(line2), local_tz=CST)
    assert r.dates == ("2026-07-16",)


def test_start_midline_drops_partial_first_line(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    line1 = _line(timestamp="2026-07-15T02:00:00Z")
    line2 = _line(timestamp="2026-07-16T02:00:00Z")
    p.write_bytes(line1 + line2)
    r = extract_activity_dates(p, 5, len(line1) + len(line2), local_tz=CST)
    assert r.dates == ("2026-07-16",)


def test_end_midline_drops_partial_last_line(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    line1 = _line(timestamp="2026-07-15T02:00:00Z")
    line2 = _line(timestamp="2026-07-16T02:00:00Z")
    p.write_bytes(line1 + line2)

    r = extract_activity_dates(p, 0, len(line1) + 5, local_tz=CST)

    assert r.dates == ("2026-07-15",)


def test_corrupt_line_does_not_hide_valid_events(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    before = _line(timestamp="2026-07-15T02:00:00Z")
    after = _line(timestamp="2026-07-16T02:00:00Z")
    p.write_bytes(before + b"{not-json}\n" + after)

    r = extract_activity_dates(p, 0, p.stat().st_size, local_tz=CST)

    assert r.dates == ("2026-07-15", "2026-07-16")


def test_assistant_events_counted(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(_line(type="assistant", timestamp="2026-07-16T02:00:00Z"))
    data = p.read_bytes()
    r = extract_activity_dates(p, 0, len(data), local_tz=CST)
    assert r.dates == ("2026-07-16",)


def test_non_timestamped_lines_ignored_for_basis(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(_line(type="user", timestamp=""))
    data = p.read_bytes()
    r = extract_activity_dates(
        p, 0, len(data), last_completed_at="2026-07-16T10:00:00Z", local_tz=CST
    )
    assert r.dates == ("2026-07-16",)
    assert r.basis == "completed_at"


def test_fallback_registered_at(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(b"")
    r = extract_activity_dates(
        p, 0, 0, registered_at="2026-07-16T10:00:00Z", local_tz=CST
    )
    assert r.dates == ("2026-07-16",)
    assert r.basis == "registered_at"


def test_no_dates_when_nothing_available(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(b"")
    r = extract_activity_dates(p, 0, 0, local_tz=CST)
    assert r.dates == ()


def test_completed_at_preferred_over_registered(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(b"")
    r = extract_activity_dates(
        p,
        0,
        0,
        last_completed_at="2026-07-16T10:00:00Z",
        registered_at="2026-07-15T10:00:00Z",
        local_tz=CST,
    )
    assert r.dates == ("2026-07-16",)
    assert r.basis == "completed_at"


def test_missing_jsonl_with_path_falls_back(tmp_path: Path) -> None:
    p = tmp_path / "gone.jsonl"
    r = extract_activity_dates(
        str(p),
        0,
        100,
        last_completed_at="2026-07-16T10:00:00Z",
        local_tz=CST,
    )
    assert r.dates == ("2026-07-16",)
    assert r.basis == "completed_at"


def test_empty_path_returns_empty_no_fallback() -> None:
    r = extract_activity_dates(
        "", 0, 100, last_completed_at="2026-07-16T10:00:00Z", local_tz=CST
    )
    assert r.dates == ()
