"""Profile 会话提示词渲染测试。"""

from pathlib import Path

from trowel_py.profile.models import Profile
from trowel_py.profile.rendering import render_profile
from trowel_py.profile.repository import ProfileRepository


def _write_profile(root: Path, **dimensions: str) -> ProfileRepository:
    """写入测试画像并返回同一仓储。"""
    repository = ProfileRepository(root)
    repository.write_profile(
        Profile(updated="2026-07-14", **dimensions),
        source="user-edit",
    )
    return repository


def test_render_profile_all_five_dims(tmp_path: Path) -> None:
    repository = _write_profile(
        tmp_path,
        ability="ABILITY_MARKER",
        methodology="METHOD_MARKER",
        expression="EXPR_MARKER",
        goal="GOAL_MARKER",
        other="OTHER_MARKER",
    )

    output = render_profile(repository)

    assert "# 用户画像" in output
    titles = ("能力水平", "方法论偏好", "表达风格", "长程目标", "其他")
    for title in titles:
        assert f"## {title}" in output
    positions = [output.index(f"## {title}") for title in titles]
    assert positions == sorted(positions)
    assert "ABILITY_MARKER" in output
    assert "OTHER_MARKER" in output


def test_render_profile_empty_when_no_file(tmp_path: Path) -> None:
    assert render_profile(ProfileRepository(tmp_path)) == ""


def test_render_profile_empty_when_all_dims_blank(tmp_path: Path) -> None:
    repository = _write_profile(tmp_path)

    assert render_profile(repository) == ""


def test_render_profile_skips_empty_dims(tmp_path: Path) -> None:
    repository = _write_profile(
        tmp_path,
        ability="ABILITY_MARKER",
        goal="GOAL_MARKER",
    )

    output = render_profile(repository)

    assert "# 用户画像" in output
    assert "## 能力水平" in output
    assert "## 长程目标" in output
    assert "ABILITY_MARKER" in output
    assert "GOAL_MARKER" in output
    assert output.index("能力水平") < output.index("长程目标")
    assert "## 方法论偏好" not in output
    assert "## 表达风格" not in output
    assert "## 其他" not in output
