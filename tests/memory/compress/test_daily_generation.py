from pathlib import Path

from trowel_py.memory.compress import compress_daily, write_fallback_daily
from trowel_py.memory.store import MemoryStore

from .support import (
    FakeProvider,
    items_json,
    require_daily_frontmatter,
    structured_episode,
)


def test_compress_daily_retries_on_bad_source_then_succeeds(tmp_path: Path) -> None:
    structured_episode(tmp_path, "s1", outcomes=("真结果",))
    provider = FakeProvider(
        responses=[
            items_json(("outcome", "真结果", "FAKE-SOURCE")),
            items_json(("outcome", "真结果", "s1:0:end")),
        ]
    )
    compress_daily(tmp_path, "2026-07-01", provider)
    assert len(provider.calls) == 2
    assert "- 真结果" in MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert (
        require_daily_frontmatter(tmp_path, "2026-07-01")["generation_status"]
        == "ok"
    )


def test_compress_daily_retries_when_model_omits_source_progress(
    tmp_path: Path,
) -> None:
    structured_episode(
        tmp_path,
        "s1",
        outcomes=("完成了真实实现",),
        corrections=("原判断已被更正",),
    )
    provider = FakeProvider(
        responses=[
            items_json(("correction", "原判断已被更正", "S1")),
            items_json(
                ("outcome", "完成了真实实现", "S1"),
                ("correction", "原判断已被更正", "S1"),
            ),
        ]
    )

    compress_daily(tmp_path, "2026-07-01", provider)

    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(provider.calls) == 2
    assert "## 进展" in body
    assert "## 更正" in body


def test_compress_daily_uses_short_source_aliases_for_llm_output(
    tmp_path: Path,
) -> None:
    """短别名避免模型复制长 segment id 时丢失 offset 后缀。"""
    structured_episode(tmp_path, "real-session-id", outcomes=("完成真实结果",))
    provider = FakeProvider(items_json(("outcome", "完成真实结果", "S1")))

    compress_daily(tmp_path, "2026-07-01", provider)

    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert "完成真实结果" in body
    assert (
        require_daily_frontmatter(tmp_path, "2026-07-01")["generation_status"]
        == "ok"
    )
    assert "【segment S1】" in provider.calls[0][1]


def test_compress_daily_budget_drops_whole_items(tmp_path: Path) -> None:
    structured_episode(tmp_path, "s1", outcomes=("a", "b"))
    big = "结果描述文本块" * 60
    provider = FakeProvider(
        items_json(
            ("outcome", big + "第一项标记ONE", "s1:0:end"),
            ("outcome", big + "第二项标记TWO", "s1:0:end"),
        )
    )
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(body) <= 800
    assert ("ONE" in body) ^ ("TWO" in body)
    assert "…" not in body


def test_compress_daily_budget_keeps_correction_and_one_outcome(
    tmp_path: Path,
) -> None:
    structured_episode(tmp_path, "s1", outcomes=("r",), corrections=("c",))
    big = "结果描述文本块" * 40
    provider = FakeProvider(
        items_json(
            ("outcome", big + "OUTCOME_ONE", "s1:0:end"),
            ("outcome", big + "OUTCOME_TWO", "s1:0:end"),
            ("correction", big + "CORRECTION_MARKER", "s1:0:end"),
        )
    )
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(body) <= 800
    assert "CORRECTION_MARKER" in body
    assert ("OUTCOME_ONE" in body) ^ ("OUTCOME_TWO" in body)


def test_compress_daily_budget_does_not_starve_progress_section(
    tmp_path: Path,
) -> None:
    """繁忙日期也必须为每个有来源的 section 保留至少一项。"""
    structured_episode(tmp_path, "s1", outcomes=("r",))
    long = "更正或待续的完整描述" * 28
    provider = FakeProvider(
        items_json(
            ("outcome", "完成了当天关键实现并通过验证", "s1:0:end"),
            ("correction", long + "更正一", "s1:0:end"),
            ("correction", long + "更正二", "s1:0:end"),
            ("open_loop", long + "待续一", "s1:0:end"),
        )
    )

    compress_daily(tmp_path, "2026-07-01", provider)

    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(body) <= 800
    assert "## 进展" in body
    assert "## 更正" in body
    assert "## 待续" in body


def test_compress_daily_omits_empty_sections(tmp_path: Path) -> None:
    structured_episode(tmp_path, "s1", outcomes=("只有进展",))
    provider = FakeProvider(items_json(("outcome", "只有进展", "s1:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert "## 进展" in body
    assert "## 更正" not in body
    assert "## 待续" not in body


def test_compress_daily_dedup_same_fact_keeps_all_sources(
    tmp_path: Path,
) -> None:
    structured_episode(
        tmp_path,
        "s1",
        outcomes=("完成了 X",),
        segment_id="s1:0:end",
        registered_at="2026-07-01T09:00:00",
    )
    structured_episode(
        tmp_path,
        "s2",
        outcomes=("完成了 X",),
        segment_id="s2:0:end",
        registered_at="2026-07-01T10:00:00",
    )
    provider = FakeProvider(
        items_json(
            ("outcome", "完成了 X", "s1:0:end"),
            ("outcome", "完成了 X", "s2:0:end"),
        )
    )
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert body.count("完成了 X") == 1
    frontmatter = require_daily_frontmatter(tmp_path, "2026-07-01")
    assert set(frontmatter["source_segments"]) == {"s1:0:end", "s2:0:end"}


def test_compress_daily_fallback_on_provider_exception(tmp_path: Path) -> None:
    structured_episode(
        tmp_path,
        "s1",
        outcomes=("一个真实结果" * 50,),
    )
    provider = FakeProvider(raise_on=RuntimeError("provider down"))
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    frontmatter = require_daily_frontmatter(tmp_path, "2026-07-01")
    assert frontmatter["generation_status"] == "fallback"
    assert "一个真实结果" not in body
    assert "s1:0:end" in body


def test_compress_daily_fallback_on_bad_json_twice(tmp_path: Path) -> None:
    structured_episode(tmp_path, "s1", outcomes=("x",))
    provider = FakeProvider(responses=["not json at all", "{still not json"])
    compress_daily(tmp_path, "2026-07-01", provider)
    assert len(provider.calls) == 2
    assert (
        require_daily_frontmatter(tmp_path, "2026-07-01")["generation_status"]
        == "fallback"
    )


def test_compress_daily_legacy_events_episode_still_works(tmp_path: Path) -> None:
    structured_episode(tmp_path, "s1", events="卡两小时在浏览器缓存")
    provider = FakeProvider(
        items_json(("open_loop", "浏览器缓存问题待复测", "s1:0:end"))
    )
    compress_daily(tmp_path, "2026-07-01", provider)
    assert "浏览器缓存" in provider.calls[0][1]


def test_write_fallback_daily_no_episodes_is_noop(tmp_path: Path) -> None:
    assert write_fallback_daily(tmp_path, "2026-07-01") == ""
    assert not (tmp_path / "diary" / "daily" / "2026-07-01.md").exists()


def test_write_fallback_daily_marks_status_and_points_at_sources(
    tmp_path: Path,
) -> None:
    structured_episode(tmp_path, "s1", outcomes=("x",))
    assert write_fallback_daily(tmp_path, "2026-07-01") == "2026-07-01"
    frontmatter = require_daily_frontmatter(tmp_path, "2026-07-01")
    assert frontmatter["generation_status"] == "fallback"
    assert frontmatter["source_segments"] == ["s1:0:end"]


def test_compress_daily_single_oversized_item_falls_back(
    tmp_path: Path,
) -> None:
    structured_episode(tmp_path, "s1", outcomes=("x",))
    huge = "巨" * 900
    provider = FakeProvider(
        items_json(("outcome", huge + "MARKER", "s1:0:end"))
    )
    compress_daily(tmp_path, "2026-07-01", provider)
    assert (
        require_daily_frontmatter(tmp_path, "2026-07-01")["generation_status"]
        == "fallback"
    )
