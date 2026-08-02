from __future__ import annotations

from pathlib import Path

from trowel_py.memory.injection import build_memory_injection

from .support import item, write_core, write_diary, write_l0, write_profile


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
