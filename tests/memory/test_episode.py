"""tests for the episode store + derived daily (slice-040-a P1).

The episode model fixes P1 (diary overwrite = lost experience): one source
session maps to exactly one ``episodes/<cc_session_id>.md`` file, so two
sessions reviewed the same day can no longer overwrite each other. The daily
file becomes a *derived aggregate* of all episodes for that review_date, not a
per-session overwrite target.

Fixture sessions mirror the real sessions.db shape (cc_session_id / workdir /
jsonl_path), with controlled diary content so assertions stay deterministic.
"""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext


def _ctx(
    sid: str,
    *,
    review_date: str = "2026-07-09",
    workdir: str = "/proj",
    registered_at: str = "2026-07-09T10:00:00",
    segment_id: str | None = None,
    activity_dates: tuple[str, ...] = (),
    date_basis: str = "",
    processed_date: str = "",
) -> PersistContext:
    return PersistContext(
        segment_id=segment_id or f"{sid}:0:end",
        cc_session_id=sid,
        workdir=workdir,
        registered_at=registered_at,
        review_date=review_date,
        source_jsonl=f"/jsonl/{sid}.jsonl",
        activity_dates=activity_dates,
        date_basis=date_basis,
        processed_date=processed_date,
    )


# ---------- P1: per-session files, no overwrite ----------


def test_write_episode_creates_per_session_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), (DraftDiary(date="2026-07-09", events="s1 body"),))
    store.write_episode(_ctx("s2"), (DraftDiary(date="2026-07-09", events="s2 body"),))
    episodes = sorted((tmp_path / "episodes").glob("*.md"))
    assert [p.stem for p in episodes] == ["s1", "s2"]


def test_three_sessions_same_day_do_not_overwrite(tmp_path: Path) -> None:
    # P1 回归：3 个 session 同日 review → 3 个 episode 文件各自存在、内容不互相覆盖。
    store = MemoryStore(tmp_path)
    for i, sid in enumerate(["s1", "s2", "s3"]):
        store.write_episode(
            _ctx(sid, workdir=f"/proj{i}", registered_at=f"2026-07-09T1{i}:00:00"),
            (DraftDiary(date="2026-07-09", events=f"session {sid} 独有经历"),),
        )
    episodes = sorted((tmp_path / "episodes").glob("*.md"))
    assert len(episodes) == 3
    texts = [p.read_text(encoding="utf-8") for p in episodes]
    # every session's experience survives — none was overwritten by a later one
    assert any("session s1 独有经历" in t for t in texts)
    assert any("session s2 独有经历" in t for t in texts)
    assert any("session s3 独有经历" in t for t in texts)


def test_write_episode_frontmatter_carries_provenance(tmp_path: Path) -> None:
    # C-1 / 接口契约: frontmatter carries cc_session_id / workdir / review_date
    # / activity_dates / source_jsonl / segments[].
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", workdir="/proj", review_date="2026-07-09"),
        (DraftDiary(date="2026-07-09", events="事件"),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "cc_session_id: s1" in text
    assert "workdir: /proj" in text
    assert "review_date: '2026-07-09'" in text or "review_date: 2026-07-09" in text
    assert "activity_dates" in text
    assert "source_jsonl: /jsonl/s1.jsonl" in text
    assert "segments:" in text
    assert "segment_id: 's1:0:end'" in text or "segment_id: s1:0:end" in text


def test_segment_meta_carries_activity_dates_and_basis(tmp_path: Path) -> None:
    """slice-061 block-2: per-segment metadata carries activity_dates + basis +
    processed_date, so the daily projection (block-4) routes by the real date
    instead of the top-level review_date."""
    from trowel_py.memory.store import _split_frontmatter

    store = MemoryStore(tmp_path)
    ctx = _ctx(
        "s1",
        activity_dates=("2026-07-16", "2026-07-17"),
        date_basis="jsonl_timestamp",
        processed_date="2026-07-18",
    )
    store.write_episode(
        ctx,
        (
            DraftDiary(date="2026-07-16", events="d16"),
            DraftDiary(date="2026-07-17", events="d17"),
        ),
    )
    fm, _body = _split_frontmatter(
        (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    )
    seg = fm["segments"][0]
    assert seg["activity_dates"] == ["2026-07-16", "2026-07-17"]
    assert seg["date_basis"] == "jsonl_timestamp"
    assert seg["processed_date"] == "2026-07-18"


# --- slice-061 block-4: daily projects per-segment by real date -----------


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
    # C-2: 16号 projection must NOT carry 17号's body
    assert "17号的事" not in d16[0][1]


def test_project_review_date_no_longer_routes(tmp_path: Path) -> None:
    """C-2: top-level review_date no longer selects the whole episode. A segment
    whose activity_dates exclude the target day contributes nothing, even when
    review_date matches."""
    store = MemoryStore(tmp_path)
    ctx = _ctx("s1", review_date="2026-07-09", activity_dates=("2026-07-10",))
    store.write_episode(ctx, (DraftDiary(date="2026-07-10", events="实际10号"),))
    assert store.project_daily_entries("2026-07-09") == []
    items = store.project_daily_entries("2026-07-10")
    assert len(items) == 1


def test_project_legacy_episode_recovers_dates_from_headings(tmp_path: Path) -> None:
    """block-5 compat: an episode written without activity_dates meta still
    projects by recovering the segment's dates from its `## <date>` headings."""
    store = MemoryStore(tmp_path)
    ctx = _ctx("s1")  # no activity_dates → legacy shape
    store.write_episode(ctx, (DraftDiary(date="2026-07-09", events="旧格式"),))
    items = store.project_daily_entries("2026-07-09")
    assert len(items) == 1
    assert "旧格式" in items[0][1]


def test_project_two_segments_same_session(tmp_path: Path) -> None:
    """resume: two segments of one cc session project independently."""
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
    """slice-061 block-5: a read-only health report of attribution precision."""
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-09",)),
        (DraftDiary(date="2026-07-09", events="x"),),
    )
    store.write_episode(
        _ctx("s2"), (DraftDiary(date="2026-07-09", events="y"),)  # legacy
    )
    audit = store.audit_episode_attribution()
    assert audit["episodes"] == 2
    assert audit["with_segment_dates"] == 1
    assert audit["legacy"] == 1


def test_project_bare_body_multi_day_slices(tmp_path: Path) -> None:
    """C-2 regression (codex): a bare-body episode (no segment markers) with
    multiple ## days still slices per day — never dumps the whole multi-day
    body into one day."""
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
    assert "17号的事" not in d16[0][1]  # C-2: not dumped into 07-16
    assert len(d17) == 1 and "17号的事" in d17[0][1]


def test_write_episode_segment_upsert_preserves_others(tmp_path: Path) -> None:
    # 040-b 前向兼容: same session file, a NEW segment id replaces only that
    # segment's body; a different segment id is appended, not overwriting the
    # existing one. Original experience never lost (C-1).
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100"),
        (DraftDiary(date="2026-07-09", events="第一段经历"),),
    )
    store.write_episode(
        _ctx("s1", segment_id="s1:200:300"),
        (DraftDiary(date="2026-07-09", events="第二段经历"),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "第一段经历" in text  # first segment preserved
    assert "第二段经历" in text  # second segment added


def test_write_episode_same_segment_replaces_only_it(tmp_path: Path) -> None:
    # C-1: re-running the SAME segment id replaces that segment's content (upsert)
    # while a sibling segment stays put.
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100"),
        (DraftDiary(date="2026-07-09", events="原始"),),
    )
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100"),
        (DraftDiary(date="2026-07-09", events="更新后"),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "更新后" in text
    assert "原始" not in text  # same segment replaced, not duplicated


def test_write_episode_empty_diary_marks_empty_reason(tmp_path: Path) -> None:
    # 接口契约: agent returned 0 diary → still write a (skeletal) record for the
    # segment, flagged empty_reason (no silent skip → no lost provenance).
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), ())
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "empty_reason" in text


def test_write_episode_multi_date_diary_in_one_segment(tmp_path: Path) -> None:
    # a session whose diary spans two dates (real case: d5a5762e had
    # 07-08 + 07-09) writes both into the same segment, ordered by date.
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1"),
        (
            DraftDiary(date="2026-07-09", events="后一天"),
            DraftDiary(date="2026-07-08", events="前一天"),
        ),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    # ordered by date ascending within the segment
    assert text.index("前一天") < text.index("后一天")
    assert "2026-07-08" in text and "2026-07-09" in text


# ---------- derived daily aggregate ----------


def test_derive_daily_aggregates_all_episodes_for_review_date(tmp_path: Path) -> None:
    # P1 过渡 daily: 3 sessions same review_date → derived daily carries all 3
    # anchors (not just the last one, which was the overwrite bug).
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
    # 时间序聚合：registered_at 决定 daily 里锚点顺序（不是文件名字典序）。
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
    # only episodes whose review_date matches land in that day's daily.
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
    # C-8: daily is a derived cache. Deleting it then re-deriving reconstructs
    # an equivalent body — daily is never the source of truth.
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), (DraftDiary(date="2026-07-09", events="经历 X"),))
    store.derive_daily_from_episodes("2026-07-09")
    first = store.load_diary(layer="day")[0].body
    (tmp_path / "diary" / "daily" / "2026-07-09.md").unlink()
    store.derive_daily_from_episodes("2026-07-09")
    rebuilt = store.load_diary(layer="day")[0].body
    assert first == rebuilt


def test_derive_daily_keeps_injection_non_empty(tmp_path: Path) -> None:
    # C-5: after 040-a, load_diary (used by 039 injection) is non-empty as long
    # as at least one episode exists for the day.
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), (DraftDiary(date="2026-07-09", events="注入内容"),))
    store.derive_daily_from_episodes("2026-07-09")
    assert store.load_diary(layer="day")
    assert "注入内容" in store.load_diary(layer="day")[0].body


def test_derive_daily_no_episodes_is_noop(tmp_path: Path) -> None:
    # no episodes for that date → no daily file written (don't fabricate empty).
    store = MemoryStore(tmp_path)
    store.derive_daily_from_episodes("2026-07-09")
    assert not (tmp_path / "diary" / "daily" / "2026-07-09.md").exists()
