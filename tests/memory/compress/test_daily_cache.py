from pathlib import Path

from trowel_py.memory.compress import (
    compress_daily,
    daily_dates_needing_rebuild,
    write_fallback_daily,
)
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter

from .support import (
    FakeProvider,
    daily,
    daily_frontmatter,
    items_json,
    structured_episode,
    write_legacy_episode,
)


def test_compress_daily_writes_fixed_markdown_and_frontmatter(
    tmp_path: Path,
) -> None:
    structured_episode(
        tmp_path,
        "s1",
        outcomes=("完成了 daily 重写",),
        corrections=("原来以为 X -> 现确认 Y",),
        open_loops=("weekly 重写未做",),
    )
    provider = FakeProvider(
        items_json(
            ("outcome", "完成了 daily 重写", "s1:0:end"),
            ("correction", "原来以为 X -> 现确认 Y", "s1:0:end"),
            ("open_loop", "weekly 重写未做", "s1:0:end"),
        )
    )
    assert compress_daily(tmp_path, "2026-07-01", provider) == "2026-07-01"
    [entry] = MemoryStore(tmp_path).load_diary(layer="day")
    assert entry.body.startswith("# 2026-07-01")
    assert "## 进展" in entry.body
    assert "## 更正" in entry.body
    assert "## 待续" in entry.body
    assert "- 完成了 daily 重写" in entry.body
    frontmatter = daily_frontmatter(tmp_path, "2026-07-01")
    assert frontmatter and frontmatter["generation_status"] == "ok"
    assert frontmatter["source_segments"] == ["s1:0:end"]
    assert frontmatter["source_hash"]
    assert frontmatter["generated_at"]


def test_compress_daily_no_episodes_returns_empty(tmp_path: Path) -> None:
    provider = FakeProvider()
    assert compress_daily(tmp_path, "2026-07-01", provider) == ""
    assert provider.calls == []
    assert not (tmp_path / "diary" / "daily" / "2026-07-01.md").exists()


def test_compress_daily_idempotent_skips_llm(tmp_path: Path) -> None:
    structured_episode(tmp_path, "s1", outcomes=("完成了 X",))
    provider = FakeProvider(items_json(("outcome", "完成了 X", "s1:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider)
    first_calls = len(provider.calls)
    first_body = MemoryStore(tmp_path).load_diary(layer="day")[0].body

    provider2 = FakeProvider(
        items_json(("outcome", "SHOULD NOT BE USED", "s1:0:end"))
    )
    compress_daily(tmp_path, "2026-07-01", provider2)
    assert provider2.calls == []
    assert MemoryStore(tmp_path).load_diary(layer="day")[0].body == first_body
    assert first_calls >= 1


def test_daily_rebuild_scan_finds_missing_fallback_and_legacy_days(
    tmp_path: Path,
) -> None:
    structured_episode(tmp_path, "missing", date="2026-07-01", outcomes=("结果一",))
    structured_episode(tmp_path, "fallback", date="2026-07-02", outcomes=("结果二",))
    structured_episode(tmp_path, "legacy", date="2026-07-03", outcomes=("结果三",))
    write_fallback_daily(tmp_path, "2026-07-02")
    daily(tmp_path, "2026-07-03", "# 错写成前一天的旧版日记")
    write_legacy_episode(
        tmp_path,
        "oldest",
        "2026-07-04",
        "最老版本没有 activity_dates，也没有日期标题。",
    )

    assert daily_dates_needing_rebuild(tmp_path) == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    ]


def test_current_generation_version_is_not_rebuilt_when_sources_unchanged(
    tmp_path: Path,
) -> None:
    structured_episode(tmp_path, "s1", outcomes=("完成结果",))
    provider = FakeProvider(items_json(("outcome", "完成结果", "S1")))
    compress_daily(tmp_path, "2026-07-01", provider)

    frontmatter = daily_frontmatter(tmp_path, "2026-07-01")
    assert frontmatter and isinstance(frontmatter.get("generation_version"), int)
    assert daily_dates_needing_rebuild(tmp_path) == []


def test_failed_version_upgrade_keeps_previous_usable_daily(
    tmp_path: Path,
) -> None:
    structured_episode(tmp_path, "s1", outcomes=("已经存在的进展",))
    compress_daily(
        tmp_path,
        "2026-07-01",
        FakeProvider(items_json(("outcome", "已经存在的进展", "S1"))),
    )
    path = tmp_path / "diary" / "daily" / "2026-07-01.md"
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert frontmatter
    frontmatter["generation_version"] = 1
    path.write_text(_dump_frontmatter(frontmatter, body), encoding="utf-8")

    compress_daily(
        tmp_path,
        "2026-07-01",
        FakeProvider(raise_on=RuntimeError("provider down during migration")),
    )

    frontmatter_after, body_after = _split_frontmatter(
        path.read_text(encoding="utf-8")
    )
    assert frontmatter_after and frontmatter_after["generation_status"] == "ok"
    assert frontmatter_after["generation_version"] == 1
    assert body_after == body


def test_compress_daily_rebuilds_when_source_changes(tmp_path: Path) -> None:
    structured_episode(tmp_path, "s1", outcomes=("旧结果",))
    initial_provider = FakeProvider(
        items_json(("outcome", "旧结果", "s1:0:end"))
    )
    compress_daily(tmp_path, "2026-07-01", initial_provider)
    structured_episode(
        tmp_path,
        "s2",
        outcomes=("新结果",),
        segment_id="s2:0:end",
        registered_at="2026-07-01T12:00:00",
    )
    provider = FakeProvider(items_json(("outcome", "新结果", "s2:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider)
    assert provider.calls
    assert "新结果" in MemoryStore(tmp_path).load_diary(layer="day")[0].body
