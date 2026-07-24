"""Episode 的日期投影、legacy fallback 与 daily 派生。"""

from pathlib import Path

from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.store import MemoryStore

from .support import _ctx


def test_project_single_segment_single_day(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    ctx = _ctx("s1", activity_dates=("2026-07-09",), date_basis="jsonl_timestamp")
    store.write_episode(ctx, (DraftDiary(date="2026-07-09", events="那天的事"),))
    items = store.project_daily_entries("2026-07-09")
    assert len(items) == 1
    assert "那天的事" in items[0][1]


def test_project_cross_day_segment_splits_entries(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    ctx = _ctx("s1", activity_dates=("2026-07-16", "2026-07-17"))
    store.write_episode(
        ctx,
        (
            DraftDiary(date="2026-07-16", events="16号的事"),
            DraftDiary(date="2026-07-17", events="17号的事"),
        ),
    )
    d16 = store.project_daily_entries("2026-07-16")
    d17 = store.project_daily_entries("2026-07-17")
    assert len(d16) == 1 and "16号的事" in d16[0][1]
    assert len(d17) == 1 and "17号的事" in d17[0][1]

    assert "17号的事" not in d16[0][1]


def test_project_review_date_no_longer_routes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    ctx = _ctx("s1", review_date="2026-07-09", activity_dates=("2026-07-10",))
    store.write_episode(ctx, (DraftDiary(date="2026-07-10", events="实际10号"),))
    assert store.project_daily_entries("2026-07-09") == []
    items = store.project_daily_entries("2026-07-10")
    assert len(items) == 1


def test_project_legacy_episode_recovers_dates_from_headings(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    ctx = _ctx("s1")
    store.write_episode(ctx, (DraftDiary(date="2026-07-09", events="旧格式"),))
    items = store.project_daily_entries("2026-07-09")
    assert len(items) == 1
    assert "旧格式" in items[0][1]


def test_project_two_segments_same_session(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100", activity_dates=("2026-07-09",)),
        (DraftDiary(date="2026-07-09", events="第一段"),),
    )
    store.write_episode(
        _ctx("s1", segment_id="s1:100:200", activity_dates=("2026-07-10",)),
        (DraftDiary(date="2026-07-10", events="第二段"),),
    )
    d09 = store.project_daily_entries("2026-07-09")
    d10 = store.project_daily_entries("2026-07-10")
    assert len(d09) == 1 and "第一段" in d09[0][1]
    assert len(d10) == 1 and "第二段" in d10[0][1]


def test_audit_counts_segment_dates_vs_legacy(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-09",)),
        (DraftDiary(date="2026-07-09", events="x"),),
    )
    store.write_episode(_ctx("s2"), (DraftDiary(date="2026-07-09", events="y"),))
    audit = store.audit_episode_attribution()
    assert audit["episodes"] == 2
    assert audit["with_segment_dates"] == 1
    assert audit["legacy"] == 1


def test_project_bare_body_multi_day_slices(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    ep = tmp_path / "episodes" / "s1.md"
    ep.parent.mkdir(parents=True, exist_ok=True)
    ep.write_text(
        "---\n"
        "type: episode\ncc_session_id: s1\nreview_date: '2026-07-09'\n"
        "activity_dates:\n  - '2026-07-16'\n  - '2026-07-17'\nsegments: []\n"
        "---\n"
        "## 2026-07-16\n\n16号的事\n\n## 2026-07-17\n\n17号的事\n",
        encoding="utf-8",
    )
    d16 = store.project_daily_entries("2026-07-16")
    d17 = store.project_daily_entries("2026-07-17")
    assert len(d16) == 1 and "16号的事" in d16[0][1]
    assert "17号的事" not in d16[0][1]
    assert len(d17) == 1 and "17号的事" in d17[0][1]


def test_derive_daily_aggregates_all_episodes_for_review_date(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    for i, sid in enumerate(["s1", "s2", "s3"]):
        store.write_episode(
            _ctx(sid, registered_at=f"2026-07-09T1{i}:00:00"),
            (DraftDiary(date="2026-07-09", events=f"session {sid} 锚点"),),
        )
    store.derive_daily_from_episodes("2026-07-09")
    [d] = store.load_diary(layer="day")
    assert d.date == "2026-07-09"
    body = d.body
    assert "session s1 锚点" in body
    assert "session s2 锚点" in body
    assert "session s3 锚点" in body


def test_derive_daily_orders_episodes_by_registered_at(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("z-last", registered_at="2026-07-09T20:00:00"),
        (DraftDiary(date="2026-07-09", events="LATE"),),
    )
    store.write_episode(
        _ctx("a-first", registered_at="2026-07-09T01:00:00"),
        (DraftDiary(date="2026-07-09", events="EARLY"),),
    )
    store.derive_daily_from_episodes("2026-07-09")
    [d] = store.load_diary(layer="day")
    assert d.body.index("EARLY") < d.body.index("LATE")


def test_derive_daily_filters_by_review_date(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", review_date="2026-07-09"),
        (DraftDiary(date="2026-07-09", events=" belongs-09 "),),
    )
    store.write_episode(
        _ctx("s2", review_date="2026-07-10"),
        (DraftDiary(date="2026-07-10", events=" belongs-10 "),),
    )
    store.derive_daily_from_episodes("2026-07-09")
    [d] = store.load_diary(layer="day")
    assert "belongs-09" in d.body
    assert "belongs-10" not in d.body


def test_derive_daily_rebuildable_from_episodes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), (DraftDiary(date="2026-07-09", events="经历 X"),))
    store.derive_daily_from_episodes("2026-07-09")
    first = store.load_diary(layer="day")[0].body
    (tmp_path / "diary" / "daily" / "2026-07-09.md").unlink()
    store.derive_daily_from_episodes("2026-07-09")
    rebuilt = store.load_diary(layer="day")[0].body
    assert first == rebuilt


def test_derive_daily_keeps_injection_non_empty(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), (DraftDiary(date="2026-07-09", events="注入内容"),))
    store.derive_daily_from_episodes("2026-07-09")
    assert store.load_diary(layer="day")
    assert "注入内容" in store.load_diary(layer="day")[0].body


def test_derive_daily_no_episodes_is_noop(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.derive_daily_from_episodes("2026-07-09")
    assert not (tmp_path / "diary" / "daily" / "2026-07-09.md").exists()
