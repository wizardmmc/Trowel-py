from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.hub import (
    SessionHub,
    SessionNotFoundError,
)
from trowel_py.agent_host.store import BindingStore
from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    cc_req,
    codex_req,
    make_cc_opener,
)


async def test_delete_codex_unregisters_from_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):

    binding = hub.create(codex_req(workdir))
    sid = binding.session_id
    assert sid in codex_mgr.sessions
    assert await hub.delete(sid) is True
    assert sid not in codex_mgr.sessions


async def test_delete_cc_clears_cc_multiopen_state(
    hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
):

    from trowel_py.cc_host import routes as cc_routes

    monkeypatch.setattr(cc_routes, "_WORKDIR_INDEX", {})
    monkeypatch.setattr(cc_routes, "_SESSION_NAMES", {})
    binding = hub.create(cc_req(workdir))
    sid = binding.session_id
    cc_routes._WORKDIR_INDEX.setdefault(str(workdir), set()).add(sid)
    cc_routes._SESSION_NAMES[sid] = "proj"
    await hub.delete(sid)
    assert sid not in cc_routes._SESSION_NAMES
    assert sid not in cc_routes._WORKDIR_INDEX.get(str(workdir), set())


def test_activate_cc_mirrors_legacy_active_sid(
    hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
):

    from trowel_py.cc_host import routes as cc_routes

    monkeypatch.setattr(cc_routes, "_ACTIVE_SID", None)
    cc = hub.create(cc_req(workdir))
    cx = hub.create(codex_req(workdir))
    hub.activate(cc.session_id)
    assert cc_routes._ACTIVE_SID == cc.session_id

    hub.activate(cx.session_id)
    assert cc_routes._ACTIVE_SID == cc.session_id


def test_activate_cc_uses_public_active_setter(
    hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
):
    from trowel_py.cc_host import routes as cc_routes

    activated: list[str | None] = []
    monkeypatch.setattr(cc_routes, "set_active_session_id", activated.append)
    cc = hub.create(cc_req(workdir))
    cx = hub.create(codex_req(workdir))

    hub.activate(cc.session_id)
    hub.activate(cx.session_id)

    assert activated == [cc.session_id]


def test_list_active_mixes_cc_and_codex(hub: SessionHub, workdir: Path):
    cc = hub.create(cc_req(workdir))
    cx = hub.create(codex_req(workdir))
    sessions, active_id = hub.list_active()
    ids = {s["session_id"] for s in sessions}
    runtimes = {s["runtime"] for s in sessions}
    assert ids == {cc.session_id, cx.session_id}
    assert runtimes == {"claude_code", "codex"}
    assert active_id == cx.session_id


def test_activate_sets_active_id(hub: SessionHub, workdir: Path):
    cc = hub.create(cc_req(workdir))
    cx = hub.create(codex_req(workdir))
    hub.activate(cc.session_id)
    sessions, active_id = hub.list_active()
    assert active_id == cc.session_id

    # 切换 active 只改变视图焦点，不能销毁另一个 runtime 会话。
    assert cx.session_id in {s["session_id"] for s in sessions}


async def test_delete_cc_closes_host_and_drops_binding(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir))
    sid = binding.session_id
    host = cc_registry[sid]
    assert await hub.delete(sid) is True
    assert hub.get(sid) is None
    assert sid not in cc_registry
    assert host.closed is True


async def test_delete_codex_drops_binding(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir))
    sid = binding.session_id
    assert await hub.delete(sid) is True
    assert hub.get(sid) is None
    assert sid not in codex_mgr.sessions


async def test_delete_unknown_returns_false(hub: SessionHub):
    assert await hub.delete("nope") is False


def test_default_cc_registry_uses_public_getter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trowel_py.cc_host import routes as cc_routes

    registry: dict = {}
    monkeypatch.setattr(cc_routes, "get_registry", lambda: registry)

    hub = SessionHub(BindingStore(tmp_path / "bindings.db"))

    assert hub._cc_registry is registry


def test_restart_recovers_bindings_from_store(
    hub: SessionHub, workdir: Path, tmp_path: Path
):
    cc = hub.create(cc_req(workdir))
    cx = hub.create(codex_req(workdir))

    restarted = SessionHub(
        BindingStore(hub._store.path),
        codex_manager=FakeCodexManager(),
        cc_registry={},
        cc_opener=make_cc_opener({}, {}),
    )
    bindings = {b.session_id: b for b in restarted._store.list_all()}
    assert cc.session_id in bindings
    assert cx.session_id in bindings
    assert bindings[cc.session_id].runtime is Runtime.CLAUDE_CODE
    assert bindings[cx.session_id].runtime is Runtime.CODEX


async def test_interrupt_routes_to_cc(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir))
    await hub.interrupt(binding.session_id)
    assert cc_registry[binding.session_id].interrupted is True


async def test_interrupt_routes_to_codex(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir))
    await hub.interrupt(binding.session_id)
    assert binding.session_id in codex_mgr.interrupted


async def test_interrupt_unknown_session_404(hub: SessionHub):
    with pytest.raises(SessionNotFoundError, match="session nope not found"):
        await hub.interrupt("nope")
