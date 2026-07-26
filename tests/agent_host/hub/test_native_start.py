from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.agent_host.hub._support import codex_req
from trowel_py.agent_host.hub import SessionConflictError


async def test_codex_native_start_attaches_thread_without_starting_turn(
    hub, workdir: Path, codex_mgr
) -> None:
    binding = hub.create(codex_req(workdir, model_os_mcp_enabled=True))
    codex_mgr.attach_results[None] = {
        "thread": {"id": "thread-fresh"},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": str(workdir),
    }

    identity = await hub.start_native(binding.session_id)

    assert identity.native_session_id == "thread-fresh"
    assert identity.runtime_generation == hub.runtime_generation(binding.session_id)
    assert codex_mgr.sent == []
    assert hub.get(binding.session_id).native_session_id == "thread-fresh"

    with pytest.raises(SessionConflictError, match="identity changed"):
        hub.persist_native_identity(
            replace(identity, native_session_id="different-thread")
        )
