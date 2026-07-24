from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from trowel_py.model_os.types import SelfManifest, SubsystemState


def _manifest(**overrides: Any) -> SelfManifest:
    arguments: dict[str, Any] = {
        "identity": "Trowel",
        "version": "trowel-os-v0",
        "continuity_note": "本次行动是持续主体的一段活动",
        "runtime": "cc",
        "model": "claude-sonnet-5",
        "effort": None,
        "subsystems": ("memory", "profile", "model_os", "dual_runtime", "todo_loop"),
        "memory_state": SubsystemState.INJECTED,
        "profile_state": SubsystemState.INJECTED,
        "native_tools_note": "本次 runtime 的工具与 MCP 由 cc 自带，不在 trowel 层重复",
        "authorization_scope": "需批准的动作：写文件、执行 shell",
    }
    arguments.update(overrides)
    return SelfManifest(**arguments)


def test_self_manifest_is_frozen() -> None:
    manifest = _manifest()
    with pytest.raises(FrozenInstanceError):
        manifest.model = "glm-5.2"  # type: ignore[misc]


def test_location_pointers_default_to_none() -> None:
    manifest = _manifest()
    assert manifest.task_id is None
    assert manifest.episode_id is None
    assert manifest.native_session_id is None


def test_subsystem_state_enum_has_injected_and_off() -> None:
    assert SubsystemState.INJECTED.value == "injected"
    assert SubsystemState.OFF.value == "off"
    assert SubsystemState.INJECTED != SubsystemState.OFF
