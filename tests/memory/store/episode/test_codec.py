"""Structured episode Markdown 的渲染与恢复。"""

from pathlib import Path

from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.store import MemoryStore

from .support import _ctx, _structured_entry


def test_render_structured_segment_writes_field_sections(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-17",), date_basis="jsonl_timestamp"),
        (_structured_entry(),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "#### outcomes" in text
    assert "#### decisions" in text
    assert "#### corrections" in text
    assert "#### open_loops" in text
    assert "- 完成了 daily 重写" in text


def test_render_omits_empty_field_sections(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-17",)),
        (DraftDiary(date="2026-07-17", outcomes=("只有结果",)),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "#### outcomes" in text
    assert "#### decisions" not in text
    assert "#### corrections" not in text
    assert "#### open_loops" not in text


def test_render_legacy_events_still_supported(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-09",)),
        (DraftDiary(date="2026-07-09", events="卡两小时在浏览器缓存"),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "卡两小时在浏览器缓存" in text
    assert "#### " not in text


def test_project_daily_sources_recovers_structured(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx(
            "s1",
            segment_id="s1:0:100",
            activity_dates=("2026-07-17",),
            registered_at="2026-07-17T10:00:00",
        ),
        (_structured_entry(),),
    )
    sources = store.project_daily_sources("2026-07-17")
    assert len(sources) == 1
    seg_id, registered_at, entry = sources[0]
    assert seg_id == "s1:0:100"
    assert registered_at == "2026-07-17T10:00:00"
    assert entry.date == "2026-07-17"
    assert entry.outcomes == ("完成了 daily 重写", "验证到全量测试通过")
    assert entry.decisions == ("固定三问结构：进展 / 更正 / 待续",)
    assert entry.corrections == ("原来以为单 $ 零误伤 -> 实测就近配对吞整段",)
    assert entry.open_loops == ("weekly 表达重写未做",)


def test_project_daily_sources_legacy_recovers_events(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-09",)),
        (DraftDiary(date="2026-07-09", events="legacy 自由文本经历"),),
    )
    sources = store.project_daily_sources("2026-07-09")
    assert len(sources) == 1
    _seg, _reg, entry = sources[0]
    assert entry.events == "legacy 自由文本经历"
    assert entry.outcomes == ()


def test_project_daily_sources_cross_day_splits(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-16", "2026-07-17")),
        (
            DraftDiary(date="2026-07-16", outcomes=("16号结果",)),
            DraftDiary(date="2026-07-17", outcomes=("17号结果",)),
        ),
    )
    d16 = store.project_daily_sources("2026-07-16")
    d17 = store.project_daily_sources("2026-07-17")
    assert d16[0][2].outcomes == ("16号结果",)
    assert d17[0][2].outcomes == ("17号结果",)

    assert "17号结果" not in d16[0][2].outcomes


def test_project_daily_sources_empty_when_no_episodes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    assert store.project_daily_sources("2026-07-17") == []


def test_project_daily_sources_two_segments_distinct_ids(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100", activity_dates=("2026-07-09",)),
        (DraftDiary(date="2026-07-09", outcomes=("第一段结果",)),),
    )
    store.write_episode(
        _ctx(
            "s1",
            segment_id="s1:100:200",
            activity_dates=("2026-07-09",),
            registered_at="2026-07-09T12:00:00",
        ),
        (DraftDiary(date="2026-07-09", outcomes=("第二段结果",)),),
    )
    sources = store.project_daily_sources("2026-07-09")
    assert {s[0] for s in sources} == {"s1:0:100", "s1:100:200"}

    assert sources[0][2].outcomes == ("第一段结果",)


def test_structured_multiline_item_roundtrips_as_one(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-17",)),
        (DraftDiary(date="2026-07-17", outcomes=("主结论\n续接补充说明",)),),
    )
    sources = store.project_daily_sources("2026-07-17")
    assert len(sources[0][2].outcomes) == 1
    assert "主结论" in sources[0][2].outcomes[0]
    assert "补充说明" in sources[0][2].outcomes[0]


def test_parse_unknown_field_header_degrades_to_events(tmp_path: Path) -> None:
    from trowel_py.memory.store import _dump_frontmatter

    ep = tmp_path / "episodes" / "s1.md"
    ep.parent.mkdir(parents=True, exist_ok=True)
    ep.write_text(
        _dump_frontmatter(
            {
                "type": "episode",
                "cc_session_id": "s1",
                "workdir": "/tmp",
                "registered_at": "2026-07-17T10:00:00",
                "review_date": "2026-07-17",
                "activity_dates": ["2026-07-17"],
                "source_jsonl": "/tmp/x.jsonl",
                "segments": [],
            },
            "## 2026-07-17\n\n#### summary\n- 某条手写内容\n",
        ),
        encoding="utf-8",
    )
    sources = MemoryStore(tmp_path).project_daily_sources("2026-07-17")
    assert len(sources) == 1
    entry = sources[0][2]
    assert entry.events and "某条手写内容" in entry.events
