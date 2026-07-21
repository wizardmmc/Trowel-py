"""SelfManifest type structure tests (slice-085).

Spec pass criteria covered here (structure layer):
- pass 2 (dynamic sync): ``model`` / ``effort`` allow ``None`` = unknown;
- pass 3 (legal without Task/Episode): location pointers default to ``None``;
- invariants: frozen dataclass; subsystems carried as a tuple; stable
  identity and dynamic capability are separate fields so a prompt-cache
  prefix can stay stable while the dynamic tail shifts.

Assembler behaviour (rendering, M/P off non-leak, marker smoke) lives in
``test_self_assembler.py``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from trowel_py.model_os.types import SelfManifest, SubsystemState


def _manifest(**overrides) -> SelfManifest:
    """Build a SelfManifest with sane defaults; tests override what they probe."""

    base = {
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
    base.update(overrides)
    return SelfManifest(**base)


def test_self_manifest_is_frozen():
    """SelfManifest must be immutable — model cannot mutate self-representation."""

    manifest = _manifest()
    with pytest.raises(FrozenInstanceError):
        manifest.model = "glm-5.2"  # type: ignore[misc]


def test_stable_identity_fields_exist():
    """Stable identity (identity / version / continuity_note) is its own layer.

    These fields never change within a version, so a prompt-cache prefix
    built on them stays stable.
    """

    manifest = _manifest()
    assert manifest.identity == "Trowel"
    assert manifest.version == "trowel-os-v0"
    assert "持续主体" in manifest.continuity_note


def test_dynamic_capability_fields_allow_unknown():
    """pass 2: model / effort may be None when the host has not reported yet.

    None is the explicit 'unknown' marker — the assembler must not paper
    over it with a stale cached value.
    """

    manifest = _manifest(model=None, effort=None)
    assert manifest.model is None
    assert manifest.effort is None


def test_location_pointers_default_to_none():
    """pass 3: a Manifest with no Task/Episode yet is still legal.

    task_id / episode_id / native_session_id are location pointers only;
    they default to None so 090's EpisodeContext can attach them later.
    """

    manifest = _manifest()
    assert manifest.task_id is None
    assert manifest.episode_id is None
    assert manifest.native_session_id is None


def test_location_pointers_can_be_set():
    """Location pointers are settable when a Task/Episode is bound."""

    manifest = _manifest(
        task_id="task-1", episode_id="ep-1", native_session_id="sess-1"
    )
    assert manifest.task_id == "task-1"
    assert manifest.episode_id == "ep-1"
    assert manifest.native_session_id == "sess-1"


def test_subsystems_is_tuple():
    """subsystems is a tuple (hashable, frozen-friendly), not a list."""

    manifest = _manifest()
    assert isinstance(manifest.subsystems, tuple)
    assert "memory" in manifest.subsystems
    assert "profile" in manifest.subsystems


def test_subsystem_state_enum_has_injected_and_off():
    """SubsystemState distinguishes 'content injected' from 'system off'.

    v0 only needs INJECTED / OFF; later states (e.g. DEGRADED) can be added
    without rewriting call sites.
    """

    assert SubsystemState.INJECTED.value == "injected"
    assert SubsystemState.OFF.value == "off"
    assert SubsystemState.INJECTED != SubsystemState.OFF


def test_memory_and_profile_state_are_independent():
    """memory_state and profile_state are independent fields — Memory off
    does not force Profile off and vice versa (matches the slice-060 A/B
    switches being orthogonal)."""

    manifest = _manifest(
        memory_state=SubsystemState.OFF,
        profile_state=SubsystemState.INJECTED,
    )
    assert manifest.memory_state == SubsystemState.OFF
    assert manifest.profile_state == SubsystemState.INJECTED
