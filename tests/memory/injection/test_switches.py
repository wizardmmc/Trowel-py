from __future__ import annotations

from pathlib import Path

from trowel_py.memory.injection import build_memory_injection

from .support import seed_full_memory


def test_injection_defaults_to_both_on(tmp_path: Path) -> None:
    seed_full_memory(tmp_path)

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "CORE_MARKER" in output
    assert "# 用户画像" in output
    assert "PROFILE_MARKER" in output
    assert "L0_MARKER" in output
    assert "DAY_MARKER" in output
    assert "memory 根路径" in output


def test_memory_off_drops_all_memory_sections_keeps_profile(
    tmp_path: Path,
) -> None:
    seed_full_memory(tmp_path)

    output = build_memory_injection(
        "2026-07-09",
        root=tmp_path,
        memory_enabled=False,
        profile_enabled=True,
    )

    assert "# 用户画像" in output
    assert "PROFILE_MARKER" in output
    assert "CORE_MARKER" not in output
    assert "L0_MARKER" not in output
    assert "DAY_MARKER" not in output
    assert "memory 根路径" not in output
    assert "memory.search" not in output


def test_profile_off_drops_profile_section_keeps_memory(
    tmp_path: Path,
) -> None:
    seed_full_memory(tmp_path)

    output = build_memory_injection(
        "2026-07-09",
        root=tmp_path,
        memory_enabled=True,
        profile_enabled=False,
    )

    assert "CORE_MARKER" in output
    assert "L0_MARKER" in output
    assert "DAY_MARKER" in output
    assert "memory 根路径" in output
    assert "memory.search" in output
    assert "# 用户画像" not in output
    assert "PROFILE_MARKER" not in output


def test_both_off_returns_empty(tmp_path: Path) -> None:
    seed_full_memory(tmp_path)

    output = build_memory_injection(
        "2026-07-09",
        root=tmp_path,
        memory_enabled=False,
        profile_enabled=False,
    )

    assert output == ""


def test_memory_off_with_no_profile_returns_empty(tmp_path: Path) -> None:
    seed_full_memory(tmp_path)
    (tmp_path / "profile.md").unlink()

    output = build_memory_injection(
        "2026-07-09",
        root=tmp_path,
        memory_enabled=False,
        profile_enabled=True,
    )

    assert output == ""
