from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from trowel_py.agent_host.hub import (
    SessionHub,
)
from tests.agent_host.hub._support import (
    FakeCodexManager,
    codex_req,
)


def test_create_codex_refuses_when_user_config_has_same_named_mcp(
    hub: SessionHub,
    workdir: Path,
    tmp_path: Path,
) -> None:
    from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME

    (tmp_path / "config.toml").write_text(
        f"[mcp_servers.{TROWEL_NOTE_SEARCH_SERVER_NAME}]\ncommand = 'x'\n",
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as exc:
        hub.create(codex_req(workdir), request=None)
    assert exc.value.status_code == 409
    assert TROWEL_NOTE_SEARCH_SERVER_NAME in str(exc.value.detail)


def test_create_codex_allows_when_user_config_has_unrelated_mcp(
    hub: SessionHub,
    workdir: Path,
    tmp_path: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    (tmp_path / "config.toml").write_text(
        "[mcp_servers.github]\ncommand = 'gh'\n", encoding="utf-8"
    )
    binding = hub.create(codex_req(workdir), request=None)
    assert binding.session_id in codex_mgr.sessions


@pytest.mark.parametrize(
    ("memory_enabled", "profile_enabled"),
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_create_codex_four_mp_combinations_wire_injection_and_mcp(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
    memory_enabled: bool,
    profile_enabled: bool,
) -> None:

    # Hub 只装配 Memory Kernel 的输出，注入正文由 memory 测试负责。
    def fake_injection(now, root, *, memory_enabled, profile_enabled):

        return f"INJ M={memory_enabled} P={profile_enabled}"

    monkeypatch.setattr(
        "trowel_py.memory.injection.build_memory_injection", fake_injection
    )
    binding = hub.create(
        codex_req(
            workdir,
            memory_enabled=memory_enabled,
            profile_enabled=profile_enabled,
            self_enabled=False,
        ),
        request=None,
    )
    session = codex_mgr.get_session(binding.session_id)
    expected_text = f"INJ M={memory_enabled} P={profile_enabled}"
    assert session.config.developer_instructions == expected_text

    if memory_enabled:
        assert session.config.trowel_memory_mcp is not None
    else:
        assert session.config.trowel_memory_mcp is None

    from trowel_py.agent_host.hub import _injection_fingerprint

    # binding 只保存注入指纹，不能持久化正文。
    assert binding.injection_hash == _injection_fingerprint(expected_text)
    assert binding.memory_enabled == memory_enabled
    assert binding.profile_enabled == profile_enabled

    if memory_enabled:
        assert binding.declared_mcp_roster == ("trowel_note_search",)
    else:
        assert binding.declared_mcp_roster == ()


def test_create_codex_empty_injection_maps_to_none_developer_instructions(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(
        "trowel_py.memory.injection.build_memory_injection",
        lambda *a, **k: "",
    )
    binding = hub.create(
        codex_req(
            workdir, memory_enabled=False, profile_enabled=False, self_enabled=False
        ),
        request=None,
    )
    session = codex_mgr.get_session(binding.session_id)
    assert session.config.developer_instructions is None
    assert session.config.trowel_memory_mcp is None
    assert binding.injection_hash == ""


def test_create_codex_includes_self_section_when_enabled(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(
        "trowel_py.memory.injection.build_memory_injection",
        lambda *a, **k: "MEMORY_MARKER",
    )
    binding = hub.create(
        codex_req(workdir, memory_enabled=True, profile_enabled=True),
        request=None,
    )
    session = codex_mgr.get_session(binding.session_id)
    instr = session.config.developer_instructions
    assert instr is not None
    assert "# 关于你（Self" in instr
    assert "Trowel" in instr

    assert instr.index("Trowel") < instr.index("MEMORY_MARKER")
    assert binding.self_enabled is True


def test_create_codex_keeps_self_when_memory_fails(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def boom(*a, **k):
        raise RuntimeError("memory dir gone")

    monkeypatch.setattr("trowel_py.memory.injection.build_memory_injection", boom)
    binding = hub.create(
        codex_req(workdir, memory_enabled=True, profile_enabled=True),
        request=None,
    )
    session = codex_mgr.get_session(binding.session_id)
    instr = session.config.developer_instructions
    assert instr is not None
    assert "# 关于你（Self" in instr
    assert "Trowel" in instr


def test_create_codex_follow_does_not_invent_overrides(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):

    binding = hub.create(codex_req(workdir, permission_preset="follow"), request=None)
    session = codex_mgr.get_session(binding.session_id)
    assert session.config.approval_policy is None
    assert session.config.sandbox is None
    assert binding.permission_preset == "follow"
    assert binding.permission is None


@pytest.mark.parametrize(
    ("preset", "approval", "sandbox"),
    [
        ("read-only", "on-request", "read-only"),
        ("workspace-write", "on-request", "workspace-write"),
        ("danger-full-access", "never", "danger-full-access"),
    ],
)
def test_create_codex_permission_presets_are_centralized(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    preset: str,
    approval: str,
    sandbox: str,
):

    binding = hub.create(codex_req(workdir, permission_preset=preset), request=None)
    config = codex_mgr.get_session(binding.session_id).config
    assert (config.approval_policy, config.sandbox) == (approval, sandbox)
