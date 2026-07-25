from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.compress import compress_monthly, compress_weekly
from trowel_py.memory.regeneration import plan_regeneration
from trowel_py.memory.store import _dump_frontmatter, _split_frontmatter

from tests.memory.compress.support import FakeProvider

from .support import generate_day, seed_day, weekly_json


def _target_keys(plan: object) -> list[str]:
    return [f"{target.layer}:{target.period}" for target in plan.targets]


def test_missing_daily_plan_cascades_to_week_and_month(tmp_path: Path) -> None:
    seed_day(tmp_path)

    plan = plan_regeneration(
        tmp_path,
        layer="daily",
        from_period="2026-07-06",
        to_period="2026-07-06",
        mode="missing",
    )

    assert _target_keys(plan) == [
        "daily:2026-07-06",
        "weekly:2026-W28",
        "monthly:2026-07",
    ]
    assert plan.targets[0].reason == "missing"
    assert plan.targets[1].dependencies == ("daily:2026-07-06",)
    assert plan.targets[2].dependencies == ("weekly:2026-W28",)
    assert not (tmp_path / "diary" / "daily" / "2026-07-06.md").exists()
    assert (tmp_path / "meta" / "regeneration" / "plans" / f"{plan.plan_id}.json").exists()


@pytest.mark.parametrize(
    ("mode", "mutate", "selected"),
    [
        ("missing", "none", False),
        ("failed", "fallback", True),
        ("stale", "version", True),
        ("all", "none", True),
    ],
)
def test_daily_plan_modes_are_distinct(
    tmp_path: Path,
    mode: str,
    mutate: str,
    selected: bool,
) -> None:
    seed_day(tmp_path)
    generate_day(tmp_path)
    path = tmp_path / "diary" / "daily" / "2026-07-06.md"
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert frontmatter
    if mutate == "fallback":
        frontmatter["generation_status"] = "fallback"
    elif mutate == "version":
        frontmatter["generation_version"] = 1
    path.write_text(_dump_frontmatter(frontmatter, body), encoding="utf-8")

    plan = plan_regeneration(
        tmp_path,
        layer="daily",
        from_period="2026-07-06",
        to_period="2026-07-06",
        mode=mode,
    )

    assert ("daily:2026-07-06" in _target_keys(plan)) is selected


def test_daily_source_change_is_stale_and_cascades(tmp_path: Path) -> None:
    seed_day(tmp_path, text="旧结果")
    generate_day(tmp_path, text="旧结果")
    seed_day(tmp_path, text="新结果")

    plan = plan_regeneration(
        tmp_path,
        layer="daily",
        from_period="2026-07-06",
        to_period="2026-07-06",
        mode="stale",
    )

    assert _target_keys(plan) == [
        "daily:2026-07-06",
        "weekly:2026-W28",
        "monthly:2026-07",
    ]
    assert "source_hash" in plan.targets[0].differences


def test_plan_range_excludes_sources_outside_bounds(tmp_path: Path) -> None:
    seed_day(tmp_path, "2026-07-06", "A")
    seed_day(tmp_path, "2026-07-07", "B")

    plan = plan_regeneration(
        tmp_path,
        layer="daily",
        from_period="2026-07-07",
        to_period="2026-07-07",
        mode="missing",
    )

    assert "daily:2026-07-06" not in _target_keys(plan)
    assert "daily:2026-07-07" in _target_keys(plan)


def test_weekly_source_change_is_stale_and_cascades_to_month(
    tmp_path: Path,
) -> None:
    seed_day(tmp_path)
    generate_day(tmp_path)
    compress_weekly(tmp_path, "2026-W28", FakeProvider(weekly_json()))
    daily_path = tmp_path / "diary" / "daily" / "2026-07-06.md"
    frontmatter, body = _split_frontmatter(daily_path.read_text(encoding="utf-8"))
    assert frontmatter
    daily_path.write_text(
        _dump_frontmatter(frontmatter, body + "\n- 新增来源事实\n"),
        encoding="utf-8",
    )

    plan = plan_regeneration(
        tmp_path,
        layer="weekly",
        from_period="2026-W28",
        to_period="2026-W28",
        mode="stale",
    )

    assert _target_keys(plan) == ["weekly:2026-W28", "monthly:2026-07"]
    assert "source_hash" in plan.targets[0].differences


def test_monthly_generation_version_and_source_hash_are_stale(
    tmp_path: Path,
) -> None:
    seed_day(tmp_path)
    generate_day(tmp_path)
    compress_weekly(tmp_path, "2026-W28", FakeProvider(weekly_json()))
    compress_monthly(tmp_path, "2026-07", FakeProvider("本月完成 A。"))
    monthly_path = tmp_path / "diary" / "monthly" / "2026-07.md"
    frontmatter, body = _split_frontmatter(monthly_path.read_text(encoding="utf-8"))
    assert frontmatter
    frontmatter["generation_version"] = 1
    monthly_path.write_text(
        _dump_frontmatter(frontmatter, body), encoding="utf-8"
    )

    version_plan = plan_regeneration(
        tmp_path,
        layer="monthly",
        from_period="2026-07",
        to_period="2026-07",
        mode="stale",
    )

    assert _target_keys(version_plan) == ["monthly:2026-07"]
    assert "generation_version" in version_plan.targets[0].differences

    weekly_path = tmp_path / "diary" / "weekly" / "2026-W28.md"
    weekly_frontmatter, weekly_body = _split_frontmatter(
        weekly_path.read_text(encoding="utf-8")
    )
    assert weekly_frontmatter
    weekly_path.write_text(
        _dump_frontmatter(weekly_frontmatter, weekly_body + "\n- 新周事实\n"),
        encoding="utf-8",
    )
    source_plan = plan_regeneration(
        tmp_path,
        layer="monthly",
        from_period="2026-07",
        to_period="2026-07",
        mode="stale",
    )
    assert "source_hash" in source_plan.targets[0].differences


def test_invalid_plan_range_and_mode_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        plan_regeneration(
            tmp_path,
            layer="daily",
            from_period="2026-07-07",
            to_period="2026-07-06",
            mode="all",
        )
    with pytest.raises(ValueError):
        plan_regeneration(
            tmp_path,
            layer="daily",
            from_period="2026-07-06",
            to_period="2026-07-06",
            mode="changed",
        )
