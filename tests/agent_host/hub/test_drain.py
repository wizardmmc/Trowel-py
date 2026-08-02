"""验证应用退出时只收敛当前实例资源，并保留可恢复 binding。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.hub import SessionConflictError, SessionHub
from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    cc_req,
    codex_req,
)


async def test_close_all_rejects_new_work_and_preserves_bindings(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
) -> None:
    """全局 drain 关闭实时 runtime，但不能删除会话恢复记录。"""

    cc = hub.create(cc_req(workdir))
    codex = hub.create(codex_req(workdir))

    result = await hub.close_all()

    assert result[cc.session_id].status == "closed"
    assert result[codex.session_id].status == "closed"
    assert cc_registry == {}
    assert codex_mgr.sessions == {}
    assert hub.store.get(cc.session_id) == cc
    assert hub.store.get(codex.session_id) == codex
    with pytest.raises(SessionConflictError, match="应用正在退出"):
        hub.create(cc_req(workdir))
    with pytest.raises(SessionConflictError, match="应用正在退出"):
        await hub.start_codex_turn(codex.session_id, "late work")


async def test_close_all_ignores_disconnected_historical_binding(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    """应用退出只处理本实例实时 registry，不把旧 binding 当成活资源。"""

    historical = hub.create(cc_req(workdir))
    cc_registry.pop(historical.session_id)

    result = await hub.close_all()

    assert result == {}
    assert hub.store.get(historical.session_id) == historical
