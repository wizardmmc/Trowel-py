from __future__ import annotations

from pathlib import Path

from trowel_py.memory.injection import _render_profile, build_memory_injection
from trowel_py.memory.store import MemoryStore

from .support import item, write_core, write_diary, write_l0, write_profile


def test_render_profile_all_five_dims(tmp_path: Path) -> None:
    write_profile(
        tmp_path,
        ability="ABILITY_MARKER",
        methodology="METHOD_MARKER",
        expression="EXPR_MARKER",
        goal="GOAL_MARKER",
        other="OTHER_MARKER",
    )

    output = _render_profile(MemoryStore(tmp_path))

    assert "# 用户画像" in output
    titles = ("能力水平", "方法论偏好", "表达风格", "长程目标", "其他")
    for title in titles:
        assert f"## {title}" in output
    positions = [output.index(f"## {title}") for title in titles]
    assert positions == sorted(positions)
    assert "ABILITY_MARKER" in output
    assert "OTHER_MARKER" in output


def test_render_profile_empty_when_no_file(tmp_path: Path) -> None:
    assert _render_profile(MemoryStore(tmp_path)) == ""


def test_render_profile_empty_when_all_dims_blank(tmp_path: Path) -> None:
    write_profile(tmp_path)

    assert _render_profile(MemoryStore(tmp_path)) == ""


def test_render_profile_skips_empty_dims(tmp_path: Path) -> None:
    write_profile(tmp_path, ability="ABILITY_MARKER", goal="GOAL_MARKER")

    output = _render_profile(MemoryStore(tmp_path))

    assert "# 用户画像" in output
    assert "## 能力水平" in output
    assert "## 长程目标" in output
    assert "ABILITY_MARKER" in output
    assert "GOAL_MARKER" in output
    assert output.index("能力水平") < output.index("长程目标")
    assert "## 方法论偏好" not in output
    assert "## 表达风格" not in output
    assert "## 其他" not in output


def test_build_injection_includes_profile_between_core_and_l0(
    tmp_path: Path,
) -> None:
    write_core(tmp_path, [item("a", "CORE_MARKER imperative", "active")])
    write_profile(tmp_path, ability="PROFILE_MARKER")
    write_l0(tmp_path, "L0_MARKER index")

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "# 用户画像" in output
    assert "PROFILE_MARKER" in output
    assert (
        output.index("CORE_MARKER")
        < output.index("PROFILE_MARKER")
        < output.index("L0_MARKER")
    )


def test_profile_survives_when_diary_truncated(tmp_path: Path) -> None:
    write_profile(tmp_path, ability="PROFILE_SURVIVES_MARKER")
    write_diary(
        tmp_path,
        "2025-09",
        "month",
        "大" * 20000 + "BIGMONTHLY_MARKER",
    )

    output = build_memory_injection("2026-07-11", root=tmp_path)

    assert "# 用户画像" in output
    assert "PROFILE_SURVIVES_MARKER" in output
    assert "BIGMONTHLY_MARKER" not in output
