from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from trowel_py.agent_host.hub import (
    RuntimeFrozenError,
    SessionHub,
)
from tests.agent_host.hub._support import (
    FakeCodexManager,
    cc_req,
    codex_req,
)


def test_patch_runtime_change_rejected(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir), request=None)
    with pytest.raises(RuntimeFrozenError):
        hub.patch(binding.session_id, runtime="codex")


def test_patch_same_runtime_ok(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir), request=None)
    hub.patch(binding.session_id, runtime="claude_code")


async def test_codex_model_switch_queues_valid_pair_and_auto_falls_back(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):

    binding = hub.create(
        codex_req(workdir, model="gpt-5.6-sol", effort="ultra"), request=None
    )
    selected = await hub.update_codex_settings(
        binding.session_id, model="gpt-5.6-luna", effort="ultra"
    )
    assert selected == {
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "adjusted": True,
    }
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_settings() == ("gpt-5.6-luna", "medium")

    # 排队中的设置尚未被 native turn 接受，公开 binding 仍保留当前值。
    assert hub.get(binding.session_id).model == "gpt-5.6-sol"


async def test_codex_model_switch_rejects_unknown_model(hub: SessionHub, workdir: Path):

    binding = hub.create(codex_req(workdir), request=None)
    with pytest.raises(HTTPException) as exc:
        await hub.update_codex_settings(
            binding.session_id, model="not-in-catalog", effort="low"
        )
    assert exc.value.status_code == 422


async def test_codex_effort_only_uses_native_default_model_for_fresh_session(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):

    binding = hub.create(codex_req(workdir, model=None, effort=None), request=None)
    selected = await hub.update_codex_settings(
        binding.session_id, model=None, effort="ultra"
    )
    assert selected == {
        "model": "gpt-5.6-sol",
        "effort": "ultra",
        "adjusted": False,
    }
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_settings() == ("gpt-5.6-sol", "ultra")
