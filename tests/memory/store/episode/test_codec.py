"""Structured episode Markdown 的渲染与恢复。"""

from pathlib import Path

from trowel_py.memory.draft import (
    DraftCorrection,
    DraftDecision,
    DraftDiary,
    DraftEvidence,
    DraftOpenLoop,
    DraftOutcome,
)
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


def test_episode_v3_roundtrips_structured_items_and_daily_projection(
    tmp_path: Path,
) -> None:
    entry = DraftDiary(
        date="2026-07-24",
        items=(
            DraftOutcome("完成 Episode v3", "窄测通过"),
            DraftDecision(
                "生产提炼继续使用 GLM-5.1",
                "真实 A/B 的长会话召回更完整",
                "active",
            ),
            DraftDecision(
                "改用 Luna",
                "早期只看到了速度",
                "superseded",
            ),
            DraftCorrection(
                "Luna 已足够",
                "Episode 继续使用 GLM-5.1",
                "GLM 赢 5 个样本",
            ),
            DraftOpenLoop(
                "补 Codex command smoke",
                "实验样本没有 commandExecution",
                "active",
            ),
            DraftOpenLoop(
                "旧临时任务",
                "已在当前 segment 解决",
                "closed",
            ),
            DraftEvidence(
                "schema gate 通过",
                "GLM 与 Luna 都是 7/7",
            ),
        ),
    )
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", activity_dates=("2026-07-24",)),
        (entry,),
    )

    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "episode_schema_version: 3" in text
    assert "source_ref_scheme:" not in text
    assert "source_refs:" not in text
    assert "#### evidence" in text

    [source] = store.project_daily_sources("2026-07-24")
    projected = source[2]
    assert len(projected.items) == 7
    assert projected.items == entry.items
    assert projected.decisions == (
        "生产提炼继续使用 GLM-5.1（理由：真实 A/B 的长会话召回更完整）",
    )
    assert projected.corrections == (
        "原来以为 Luna 已足够，现确认 Episode 继续使用 GLM-5.1（依据：GLM 赢 5 个样本）",
    )
    assert projected.open_loops == (
        "补 Codex command smoke（原因：实验样本没有 commandExecution）",
    )


def test_episode_v2_with_source_refs_still_reads(tmp_path: Path) -> None:
    from trowel_py.memory.store import _dump_frontmatter

    segment_id = "old:0:100"
    episode_path = tmp_path / "episodes" / "old.md"
    episode_path.parent.mkdir(parents=True)
    episode_path.write_text(
        _dump_frontmatter(
            {
                "type": "episode",
                "cc_session_id": "old",
                "workdir": "/project",
                "registered_at": "2026-07-24T10:00:00",
                "review_date": "2026-07-24",
                "activity_dates": ["2026-07-24"],
                "source_jsonl": "/sessions/old.jsonl",
                "segments": [
                    {
                        "segment_id": segment_id,
                        "activity_dates": ["2026-07-24"],
                        "episode_schema_version": 2,
                        "source_ref_scheme": "nonempty_jsonl_line_v1",
                        "episode_items": [
                            {
                                "date": "2026-07-24",
                                "item": {
                                    "kind": "outcome",
                                    "summary": "旧 Episode 仍能恢复",
                                    "detail": "",
                                    "source_refs": ["L000001"],
                                },
                            }
                        ],
                    }
                ],
            },
            (
                f"<!-- @segment {segment_id} -->\n"
                "## 2026-07-24\n\n"
                "#### outcomes\n"
                "- 旧 Episode 仍能恢复; source_refs: L000001\n"
                f"<!-- @endsegment {segment_id} -->\n"
            ),
        ),
        encoding="utf-8",
    )

    [source] = MemoryStore(tmp_path).project_daily_sources("2026-07-24")
    assert source[2].items == (DraftOutcome("旧 Episode 仍能恢复", ""),)
    assert source[2].outcomes == ("旧 Episode 仍能恢复",)


def test_legacy_structured_episode_still_reads_after_v3(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("legacy", activity_dates=("2026-07-17",)),
        (DraftDiary(date="2026-07-17", outcomes=("旧结果",)),),
    )

    [source] = store.project_daily_sources("2026-07-17")
    assert source[2].items == ()
    assert source[2].outcomes == ("旧结果",)
