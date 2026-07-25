import json
from pathlib import Path

from trowel_py.memory.compress import compress_monthly, compress_weekly
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter

from .support import FakeProvider, daily


def _weekly_json(
    *items: tuple[str, str, list[str]],
    bypass: dict[str, list[dict[str, object]]] | None = None,
) -> str:
    return json.dumps(
        {
            "items": [
                {"type": item_type, "text": text, "source_days": source_days}
                for item_type, text, source_days in items
            ],
            "bypass": bypass or {},
        }
    )


def _frontmatter(path: Path) -> dict:
    frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert frontmatter is not None
    return frontmatter


def test_compress_weekly_writes_structured_weekly_and_provenance(
    tmp_path: Path,
) -> None:
    daily(tmp_path, "2026-07-06", "# 2026-07-06\n\n## 进展\n- 完成 A\n")
    daily(tmp_path, "2026-07-08", "# 2026-07-08\n\n## 待续\n- 继续 B\n")
    provider = FakeProvider(
        _weekly_json(
            ("outcome", "完成 A", ["2026-07-06"]),
            ("open_loop", "继续 B", ["2026-07-08"]),
        )
    )

    report = compress_weekly(tmp_path, "2026-W28", provider)

    assert report["weekly_written"]
    path = tmp_path / "diary" / "weekly" / "2026-W28.md"
    frontmatter = _frontmatter(path)
    assert frontmatter["source_days"] == ["2026-07-06", "2026-07-08"]
    assert frontmatter["source_hash"]
    assert frontmatter["generation_version"] >= 3
    assert frontmatter["generation_status"] == "ok"
    assert frontmatter["generated_at"]
    assert frontmatter["derivation"]["pipeline"] == "weekly-compress"
    assert frontmatter["derivation"]["generator_runtime"] == "direct_api"
    assert frontmatter["items"][0]["source_days"] == ["2026-07-06"]
    [weekly] = MemoryStore(tmp_path).load_diary(layer="week")
    assert "## 进展" in weekly.body
    assert "## 待续" in weekly.body


def test_compress_weekly_no_dailies_returns_empty(tmp_path: Path) -> None:
    provider = FakeProvider()
    report = compress_weekly(tmp_path, "2026-W28", provider)
    assert report["weekly_written"] is False
    assert provider.calls == []


def test_compress_weekly_only_matches_iso_week(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-05", "W27_SUNDAY")
    daily(tmp_path, "2026-07-06", "W28_MONDAY")
    provider = FakeProvider(
        _weekly_json(("outcome", "本周结果", ["2026-07-06"]))
    )

    compress_weekly(tmp_path, "2026-W28", provider)

    assert "W28_MONDAY" in provider.calls[0][1]
    assert "W27_SUNDAY" not in provider.calls[0][1]


def test_compress_weekly_sends_every_daily_without_slicing(
    tmp_path: Path,
) -> None:
    days = [f"2026-07-{day:02d}" for day in range(6, 13)]
    for index, day in enumerate(days):
        daily(tmp_path, day, f"## 进展\n- DAY-{index}-" + "正文" * 700)
    provider = FakeProvider(
        responses=[
            _weekly_json(
                *[
                    ("outcome", f"完成第 {index} 天", [day])
                    for index, day in enumerate(days[:5])
                ]
            ),
            _weekly_json(
                *[
                    ("outcome", f"完成第 {index} 天", [day])
                    for index, day in enumerate(days[5:], 5)
                ]
            ),
        ]
    )

    compress_weekly(tmp_path, "2026-W28", provider)

    joined_prompts = "\n".join(user for _system, user in provider.calls)
    assert all(f"DAY-{index}-" in joined_prompts for index in range(7))
    assert len(provider.calls) > 1


def test_compress_weekly_recompacts_candidates_from_multiple_chunks(
    tmp_path: Path,
) -> None:
    days = [f"2026-07-{day:02d}" for day in range(6, 13)]
    for index, day in enumerate(days):
        daily(tmp_path, day, f"## 进展\n- DAY-{index}-" + "正文" * 700)
    long = "分批候选仍然过于详细" * 45
    provider = FakeProvider(
        responses=[
            _weekly_json(
                ("outcome", long + "CHUNK_ONE", days[:5]),
            ),
            _weekly_json(
                ("outcome", long + "CHUNK_TWO", days[5:]),
            ),
            _weekly_json(
                ("outcome", "本周完成七天的主线工作", days),
            ),
        ]
    )

    report = compress_weekly(tmp_path, "2026-W28", provider)

    assert report["weekly_written"] is True
    assert len(provider.calls) == 3
    assert "分批候选" in provider.calls[2][1]
    [weekly] = MemoryStore(tmp_path).load_diary(layer="week")
    assert len(weekly.body) <= 800
    assert "本周完成七天的主线工作" in weekly.body


def test_compress_weekly_retries_illegal_source_day(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-06", "## 进展\n- 完成 A")
    provider = FakeProvider(
        responses=[
            _weekly_json(("outcome", "伪造来源", ["2026-07-05"])),
            _weekly_json(("outcome", "完成 A", ["2026-07-06"])),
        ]
    )

    report = compress_weekly(tmp_path, "2026-W28", provider)

    assert report["weekly_written"] is True
    assert len(provider.calls) == 2
    assert "source_days" in provider.calls[1][1]


def test_compress_weekly_retries_when_required_section_is_missing(
    tmp_path: Path,
) -> None:
    daily(
        tmp_path,
        "2026-07-06",
        "# 2026-07-06\n\n## 进展\n- 完成 A\n\n## 更正\n- 纠正 B\n",
    )
    provider = FakeProvider(
        responses=[
            _weekly_json(("outcome", "完成 A", ["2026-07-06"])),
            _weekly_json(
                ("outcome", "完成 A", ["2026-07-06"]),
                ("correction", "纠正 B", ["2026-07-06"]),
            ),
        ]
    )

    compress_weekly(tmp_path, "2026-W28", provider)

    assert len(provider.calls) == 2
    [weekly] = MemoryStore(tmp_path).load_diary(layer="week")
    assert "## 进展" in weekly.body
    assert "## 更正" in weekly.body


def test_compress_weekly_dedup_does_not_merge_different_item_types(
    tmp_path: Path,
) -> None:
    daily(
        tmp_path,
        "2026-07-06",
        "## 进展\n- 同一事实\n## 更正\n- 同一事实",
    )
    provider = FakeProvider(
        _weekly_json(
            ("outcome", "同一事实", ["2026-07-06"]),
            ("correction", "同一事实", ["2026-07-06"]),
        )
    )

    report = compress_weekly(tmp_path, "2026-W28", provider)

    assert report["weekly_written"] is True
    [weekly] = MemoryStore(tmp_path).load_diary(layer="week")
    assert "## 进展" in weekly.body
    assert "## 更正" in weekly.body
    assert weekly.body.count("同一事实") == 2


def test_compress_weekly_retries_when_main_items_miss_a_source_day(
    tmp_path: Path,
) -> None:
    daily(tmp_path, "2026-07-06", "## 进展\n- 完成 A")
    daily(tmp_path, "2026-07-07", "## 进展\n- 完成 B")
    provider = FakeProvider(
        responses=[
            _weekly_json(("outcome", "只写了 A", ["2026-07-06"])),
            _weekly_json(
                ("outcome", "完成 A 和 B", ["2026-07-06", "2026-07-07"])
            ),
        ]
    )

    report = compress_weekly(tmp_path, "2026-W28", provider)

    assert report["weekly_written"] is True
    assert len(provider.calls) == 2
    assert "2026-07-07" in provider.calls[1][1]


def test_compress_weekly_budget_keeps_whole_bullets_and_each_section(
    tmp_path: Path,
) -> None:
    daily(
        tmp_path,
        "2026-07-06",
        "## 进展\n- 结果\n## 更正\n- 更正\n## 待续\n- 待续",
    )
    long = "完整描述文本" * 35
    provider = FakeProvider(
        _weekly_json(
            ("outcome", long + "OUTCOME_ONE", ["2026-07-06"]),
            ("outcome", long + "OUTCOME_TWO", ["2026-07-06"]),
            ("correction", long + "CORRECTION", ["2026-07-06"]),
            ("open_loop", long + "OPEN_LOOP", ["2026-07-06"]),
        )
    )

    compress_weekly(tmp_path, "2026-W28", provider)

    [weekly] = MemoryStore(tmp_path).load_diary(layer="week")
    assert len(weekly.body) <= 800
    assert "CORRECTION" in weekly.body
    assert "OPEN_LOOP" in weekly.body
    assert ("OUTCOME_ONE" in weekly.body) ^ ("OUTCOME_TWO" in weekly.body)
    assert "…" not in weekly.body


def test_compress_weekly_budget_preserves_every_source_day(
    tmp_path: Path,
) -> None:
    days = [f"2026-07-{day:02d}" for day in range(6, 11)]
    for day in days:
        daily(tmp_path, day, f"## 进展\n- {day} 的工作")
    long = "周级工作摘要" * 20
    items = [
        ("outcome", long + f"EARLY-{index}", [days[0]])
        for index in range(4)
    ] + [
        ("outcome", long + f"DAY-{day}", [day])
        for day in days[1:]
    ]

    report = compress_weekly(
        tmp_path,
        "2026-W28",
        FakeProvider(_weekly_json(*items)),
    )

    assert report["weekly_written"] is True
    path = tmp_path / "diary" / "weekly" / "2026-W28.md"
    frontmatter = _frontmatter(path)
    selected_days = {
        day for item in frontmatter["items"] for day in item["source_days"]
    }
    assert selected_days == set(days)
    assert len(_split_frontmatter(path.read_text(encoding="utf-8"))[1]) <= 800


def test_compress_weekly_budget_prefers_what_was_done(
    tmp_path: Path,
) -> None:
    daily(
        tmp_path,
        "2026-07-06",
        "## 进展\n- 完成 A\n## 更正\n- 更正 A\n## 待续\n- 待续 A",
    )
    daily(tmp_path, "2026-07-07", "## 进展\n- 完成 B\n## 更正\n- 更正 B")
    long = "可独立理解的周级事项" * 17
    provider = FakeProvider(
        _weekly_json(
            ("correction", long + "CORRECTION_A", ["2026-07-06"]),
            ("correction", long + "CORRECTION_B", ["2026-07-07"]),
            ("outcome", long + "OUTCOME_A", ["2026-07-06"]),
            ("outcome", long + "OUTCOME_B", ["2026-07-07"]),
            ("open_loop", long + "OPEN_LOOP", ["2026-07-06"]),
        )
    )

    report = compress_weekly(tmp_path, "2026-W28", provider)

    assert report["weekly_written"] is True
    [weekly] = MemoryStore(tmp_path).load_diary(layer="week")
    assert "OUTCOME_A" in weekly.body
    assert "OUTCOME_B" in weekly.body
    assert weekly.body.count("CORRECTION_") == 1
    assert "OPEN_LOOP" in weekly.body


def test_compress_weekly_retries_when_day_coverage_cannot_fit_budget(
    tmp_path: Path,
) -> None:
    daily(tmp_path, "2026-07-06", "## 进展\n- 完成 A")
    daily(tmp_path, "2026-07-07", "## 进展\n- 完成 B")
    oversized = "过度详细的周记事项" * 55
    provider = FakeProvider(
        responses=[
            _weekly_json(
                ("outcome", oversized + "A", ["2026-07-06"]),
                ("outcome", oversized + "B", ["2026-07-07"]),
            ),
            _weekly_json(
                ("outcome", "本周完成 A 和 B", ["2026-07-06", "2026-07-07"])
            ),
        ]
    )

    report = compress_weekly(tmp_path, "2026-W28", provider)

    assert report["weekly_written"] is True
    assert len(provider.calls) == 2
    assert "800" in provider.calls[1][1]
    [weekly] = MemoryStore(tmp_path).load_diary(layer="week")
    assert len(weekly.body) <= 800
    assert "本周完成 A 和 B" in weekly.body


def test_compress_weekly_writes_source_backed_bypass_files(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-06", "## 进展\n- 完成 A")
    output = _weekly_json(
        ("outcome", "完成 A", ["2026-07-06"]),
        bypass={
            "technical-detail": [
                {"text": "技术细节正文", "source_days": ["2026-07-06"]}
            ],
            "emotional-trigger": [
                {"text": "情感场景正文", "source_days": ["2026-07-06"]}
            ],
            "cross-week-causal": [],
        },
    )

    report = compress_weekly(tmp_path, "2026-W28", FakeProvider(output))

    assert report["bypass"] == {
        "technical-detail": True,
        "emotional-trigger": True,
        "cross-week-causal": False,
    }
    bypass_root = tmp_path / "diary" / "bypass"
    technical = bypass_root / "technical-detail" / "2026-W28.md"
    assert "技术细节正文" in technical.read_text(encoding="utf-8")
    assert _frontmatter(technical)["items"][0]["source_days"] == ["2026-07-06"]
    assert (bypass_root / "emotional-trigger" / "2026-W28.md").exists()
    assert not (bypass_root / "cross-week-causal" / "2026-W28.md").exists()


def test_failed_weekly_upgrade_preserves_previous_live_file(tmp_path: Path) -> None:
    daily(tmp_path, "2026-07-06", "## 进展\n- 完成 A")
    path = tmp_path / "diary" / "weekly" / "2026-W28.md"
    path.parent.mkdir(parents=True)
    previous = _dump_frontmatter(
        {
            "type": "diary",
            "date": "2026-W28",
            "layer": "week",
            "period": "2026-W28",
            "promoted_knowledge": [],
            "source_days": ["2026-07-06"],
            "source_hash": "old-hash",
            "generation_status": "ok",
            "generation_version": 1,
        },
        "旧周记正文\n",
    )
    path.write_text(previous, encoding="utf-8")

    report = compress_weekly(
        tmp_path,
        "2026-W28",
        FakeProvider(responses=["bad", "still bad"]),
    )

    assert report["weekly_written"] is False
    assert path.read_text(encoding="utf-8") == previous


def test_compress_monthly_writes_provenance_and_source_hash(
    tmp_path: Path,
) -> None:
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

    report = compress_monthly(tmp_path, "2026-07", FakeProvider("月记正文"))

    assert report["monthly_written"] is True
    frontmatter = _frontmatter(tmp_path / "diary" / "monthly" / "2026-07.md")
    assert frontmatter["source_weeks"] == ["2026-W28"]
    assert frontmatter["source_hash"]
    assert frontmatter["generation_version"] >= 2
    assert frontmatter["generation_status"] == "ok"
    assert frontmatter["generated_at"]
    assert frontmatter["derivation"]["pipeline"] == "monthly-compress"
    assert frontmatter["derivation"]["generator_runtime"] == "direct_api"


def test_oversized_monthly_retry_failure_preserves_previous_live_file(
    tmp_path: Path,
) -> None:
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
    path = tmp_path / "diary" / "monthly" / "2026-07.md"
    path.parent.mkdir(parents=True)
    previous = _dump_frontmatter(
        {
            "type": "diary",
            "date": "2026-07",
            "layer": "month",
            "period": "2026-07",
            "promoted_knowledge": [],
        },
        "旧月记正文\n",
    )
    path.write_text(previous, encoding="utf-8")
    provider = FakeProvider(responses=["月" * 1200, "月" * 1000])

    report = compress_monthly(tmp_path, "2026-07", provider)

    assert report["monthly_written"] is False
    assert report["generation_status"] == "failed"
    assert len(provider.calls) == 2
    assert path.read_text(encoding="utf-8") == previous


def test_monthly_budget_selects_complete_sentences_across_sections(
    tmp_path: Path,
) -> None:
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
    sentence = "这是一条能够独立理解的完整月记事实。" * 15
    raw = "\n".join(
        [
            "# 本月主线",
            "MAIN_ONE。" + sentence,
            "MAIN_TWO。" + sentence,
            "## 关键更正",
            "CORRECTION。" + sentence,
            "## 待续",
            "OPEN_LOOP。" + sentence,
        ]
    )

    provider = FakeProvider(raw)
    report = compress_monthly(tmp_path, "2026-07", provider)

    assert report["monthly_written"] is True
    [monthly] = MemoryStore(tmp_path).load_diary(layer="month")
    assert len(monthly.body) <= 800
    assert "# 本月主线" in monthly.body
    assert "## 关键更正" in monthly.body
    assert "## 待续" in monthly.body
    assert "CORRECTION。" in monthly.body
    assert "OPEN_LOOP。" in monthly.body
    assert "…" not in monthly.body
    assert len(provider.calls) == 1
