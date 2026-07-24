from __future__ import annotations

from tests.model_os.self_manifest.support import build_manifest
from trowel_py.model_os.types import SubsystemState


def test_build_returns_manifest_with_stable_identity() -> None:
    manifest = build_manifest()
    assert manifest.identity == "Trowel"
    assert manifest.version
    assert "持续主体" in manifest.continuity_note


def test_build_derives_memory_state_from_switch() -> None:
    enabled = build_manifest(memory_enabled=True)
    disabled = build_manifest(memory_enabled=False)
    assert enabled.memory_state == SubsystemState.INJECTED
    assert disabled.memory_state == SubsystemState.OFF
    assert enabled.profile_state == disabled.profile_state == SubsystemState.INJECTED


def test_build_derives_profile_state_from_switch() -> None:
    enabled = build_manifest(profile_enabled=True)
    disabled = build_manifest(profile_enabled=False)
    assert enabled.profile_state == SubsystemState.INJECTED
    assert disabled.profile_state == SubsystemState.OFF
    assert enabled.memory_state == disabled.memory_state == SubsystemState.INJECTED


def test_build_carries_unknown_model_as_none() -> None:
    assert build_manifest(model=None).model is None


def test_build_default_location_pointers_none() -> None:
    manifest = build_manifest()
    assert manifest.task_id is None
    assert manifest.episode_id is None
    assert manifest.native_session_id is None


def test_build_carries_location_pointers() -> None:
    manifest = build_manifest(
        task_id="task-1", episode_id="ep-1", native_session_id="sess-1"
    )
    assert manifest.task_id == "task-1"
    assert manifest.episode_id == "ep-1"
    assert manifest.native_session_id == "sess-1"


def test_build_subsystems_use_trowel_roster() -> None:
    manifest = build_manifest()
    assert manifest.subsystems == (
        "memory",
        "profile",
        "model_os",
        "dual_runtime",
        "todo_loop",
    )
    # garden/pet 是产品功能，不属于 Trowel 身体能力清单。
    assert "garden" not in manifest.subsystems
    assert "pet" not in manifest.subsystems


def test_build_native_tools_note_mentions_runtime() -> None:
    cc = build_manifest(runtime="cc")
    codex = build_manifest(runtime="codex")
    assert "cc" in cc.native_tools_note
    assert "codex" in codex.native_tools_note


def test_build_authorization_scope_carries_preset() -> None:
    scope = build_manifest(permission_preset="follow").authorization_scope
    assert scope == "授权模式：follow"


def test_build_authorization_scope_unknown_when_no_preset() -> None:
    scope = build_manifest(permission_preset=None).authorization_scope
    assert scope == "授权模式：未定"
