from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.hub import (
    SessionHub,
)
from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    cc_req,
    codex_req,
)


def test_create_cc_session_creates_binding_and_registry_host(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir), request=None)
    assert binding.runtime is Runtime.CLAUDE_CODE
    assert binding.workdir == str(workdir)
    assert binding.session_id in cc_registry
    assert cc_registry[binding.session_id].workdir == str(workdir)
    assert hub.get(binding.session_id) is not None


def test_create_codex_session_creates_binding_and_registers_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir), request=None)
    assert binding.runtime is Runtime.CODEX
    assert binding.session_id in codex_mgr.sessions
    assert hub.get(binding.session_id) is not None


def test_create_missing_workdir_400(hub: SessionHub):
    with pytest.raises(HTTPException) as exc:
        hub.create(cc_req(Path("/nonexistent/xyz-123")), request=None)
    assert exc.value.status_code == 400


def test_create_connection_cap_409(hub: SessionHub, workdir: Path, monkeypatch):
    monkeypatch.setattr("trowel_py.agent_host.hub.MAX_CONNECTIONS", 1)
    hub.create(cc_req(workdir), request=None)
    with pytest.raises(HTTPException) as exc:
        hub.create(codex_req(workdir), request=None)
    assert exc.value.status_code == 409
