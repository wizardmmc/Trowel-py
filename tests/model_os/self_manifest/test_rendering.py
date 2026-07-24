from __future__ import annotations

import pytest

from tests.model_os.self_manifest.support import build_manifest
from trowel_py.model_os.self_assembler import render_self_injection


def test_render_returns_non_empty_string_with_identity() -> None:
    text = render_self_injection(build_manifest())
    assert isinstance(text, str)
    assert "Trowel" in text
    assert "持续主体" in text


def test_render_marks_unknown_model_explicitly() -> None:
    text = render_self_injection(build_manifest(model=None, effort="high"))
    assert "- model: 未定\n" in text


def test_render_memory_off_marks_content_not_injected() -> None:
    text = render_self_injection(build_manifest(memory_enabled=False))
    assert "- 记忆系统：跨会话记忆（本次内容未注入）" in text


def test_render_profile_off_marks_content_not_injected() -> None:
    text = render_self_injection(build_manifest(profile_enabled=False))
    assert "- 用户画像：合作者的画像（本次内容未注入）" in text


def test_render_dual_runtime_names_the_cross_runtime_boundary() -> None:
    text = render_self_injection(build_manifest(runtime="cc"))
    # 跨 runtime 是 Trowel HTTP 边界，不能伪装成宿主自带能力。
    assert "系统可分别启动 CC 与 Codex" in text
    assert "跨 runtime 调用须走 Trowel Agent HTTP API" in text
    assert "不是 runtime 自带能力" in text
    assert "双 runtime：能调用 CC 和 Codex" not in text


def test_render_has_native_tools_note() -> None:
    assert "自带" in render_self_injection(build_manifest(runtime="cc"))


def test_render_has_authorization_scope() -> None:
    text = render_self_injection(build_manifest(permission_preset="follow"))
    assert "- 授权模式：follow\n" in text


@pytest.mark.parametrize(
    ("first_overrides", "second_overrides"),
    [
        ({"model": "claude-sonnet-5"}, {"model": "glm-5.2"}),
        (
            {"memory_enabled": True, "profile_enabled": True},
            {"memory_enabled": False, "profile_enabled": False},
        ),
    ],
    ids=["model", "subsystem-switches"],
)
def test_stable_prefix_ignores_dynamic_changes(
    first_overrides: dict[str, object],
    second_overrides: dict[str, object],
) -> None:
    first = render_self_injection(build_manifest(**first_overrides))
    second = render_self_injection(build_manifest(**second_overrides))
    # 只有动态尾部可变化，稳定前缀用于保住提示词缓存。
    assert first.split("## 本次调用")[0] == second.split("## 本次调用")[0]


def test_render_includes_version_in_stable_prefix() -> None:
    manifest = build_manifest()
    stable_prefix = render_self_injection(manifest).split("## 本次调用")[0]
    assert manifest.version in stable_prefix


def test_render_includes_effort_in_dynamic_section() -> None:
    text = render_self_injection(build_manifest(effort="high"))
    assert "- effort: high\n" in text


def test_render_marks_unknown_effort_explicitly() -> None:
    text = render_self_injection(build_manifest(model="m", effort=None))
    assert "- effort: 未定\n" in text
