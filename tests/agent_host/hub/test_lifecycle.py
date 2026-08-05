from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.hub import (
    SessionConflictError,
    SessionHub,
    SessionNotFoundError,
    SessionOperationError,
)
from trowel_py.agent_host.store import BindingStore
from trowel_py.resource_lifecycle import OwnerScope, ResourceRegistry
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


@pytest.mark.parametrize(
    ("first_request", "second_request"),
    [
        (cc_req, codex_req),
        (codex_req, cc_req),
    ],
)
def test_visible_user_sessions_share_names_across_runtimes(
    hub: SessionHub,
    workdir: Path,
    first_request,
    second_request,
) -> None:
    first = hub.create(first_request(workdir))
    second = hub.create(second_request(workdir))

    assert first.name == workdir.name
    assert second.name == f"{workdir.name} #2"


@pytest.mark.parametrize("delegate_request", [cc_req, codex_req])
def test_delegate_session_does_not_occupy_user_name(
    hub: SessionHub,
    workdir: Path,
    delegate_request,
) -> None:
    hub.create(delegate_request(workdir, session_kind="delegate"))

    user = hub.create(cc_req(workdir))

    assert user.name == workdir.name


@pytest.mark.parametrize("disconnected_request", [cc_req, codex_req])
def test_disconnected_binding_does_not_occupy_user_name(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    disconnected_request,
) -> None:
    disconnected = hub.create(disconnected_request(workdir))
    if disconnected.runtime is Runtime.CLAUDE_CODE:
        cc_registry.pop(disconnected.session_id)
    else:
        codex_mgr.unregister(disconnected.session_id)

    user = hub.create(codex_req(workdir))

    assert user.name == workdir.name


async def test_deleted_session_releases_smallest_available_name(
    hub: SessionHub,
    workdir: Path,
) -> None:
    first = hub.create(cc_req(workdir))
    second = hub.create(codex_req(workdir))
    third = hub.create(codex_req(workdir))
    assert [first.name, second.name, third.name] == [
        workdir.name,
        f"{workdir.name} #2",
        f"{workdir.name} #3",
    ]

    assert await hub.delete(second.session_id) is True
    replacement = hub.create(codex_req(workdir))

    assert replacement.name == f"{workdir.name} #2"


def test_activate_sets_active_id(hub: SessionHub, workdir: Path):
    cc = hub.create(cc_req(workdir))
    cx = hub.create(codex_req(workdir))
    hub.activate(cc.session_id)
    sessions, active_id = hub.list_active()
    assert active_id == cc.session_id

    # 切换 active 只改变视图焦点，不能销毁另一个 runtime 会话。
    assert cx.session_id in {s["session_id"] for s in sessions}


@pytest.mark.parametrize("runtime", ["claude_code", "codex"])
async def test_delegate_lifecycle_stays_outside_user_projection(
    hub: SessionHub,
    workdir: Path,
    runtime: str,
):
    user = hub.create(cc_req(workdir))
    hub.activate(user.session_id)
    request = cc_req if runtime == "claude_code" else codex_req

    delegate = hub.create(request(workdir, session_kind="delegate"))

    sessions, active_id = hub.list_active()
    assert [session["session_id"] for session in sessions] == [user.session_id]
    assert active_id == user.session_id
    assert hub.get(delegate.session_id) == delegate

    await hub.interrupt(delegate.session_id)
    assert await hub.delete(delegate.session_id) is True
    assert hub.list_active()[1] == user.session_id


def test_delegate_cannot_become_current_user_session(
    hub: SessionHub,
    workdir: Path,
):
    user = hub.create(cc_req(workdir))
    delegate = hub.create(codex_req(workdir, session_kind="delegate"))

    with pytest.raises(
        SessionOperationError,
        match="delegate session cannot become the current user session",
    ):
        hub.activate(delegate.session_id)

    assert hub.list_active()[1] == user.session_id


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


async def test_cancelled_create_waiter_does_not_cancel_idempotent_operation(
    hub: SessionHub, workdir: Path
) -> None:
    """HTTP waiter 取消后创建继续，重试取得同一 binding，并在 close 后清缓存。"""

    release = asyncio.Event()
    calls = 0

    async def create_once():
        """模拟已超过 renderer 预算但仍会成功的后端创建。"""

        nonlocal calls
        calls += 1
        await release.wait()
        return hub.create(cc_req(workdir))

    first = asyncio.create_task(
        hub.coalesce_session_create("slow-create", "same", create_once)
    )
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    retry = asyncio.create_task(
        hub.coalesce_session_create("slow-create", "same", create_once)
    )
    release.set()
    binding = await retry

    assert calls == 1
    assert await hub.delete(binding.session_id) is True
    assert "slow-create" not in hub._session_create_requests


async def test_failed_idempotent_create_can_retry(
    hub: SessionHub, workdir: Path
) -> None:
    """明确失败不会把请求 ID 永久缓存。"""

    async def fail():
        """模拟创建在提交 binding 前失败。"""

        raise RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        await hub.coalesce_session_create("retryable-create", "same", fail)
    await asyncio.sleep(0)

    binding = await hub.coalesce_session_create(
        "retryable-create",
        "same",
        lambda: _create_binding(hub, workdir),
    )

    assert binding.session_id


async def test_delete_releases_ephemeral_snapshot_state(
    hub: SessionHub, workdir: Path
) -> None:
    """成功删除 binding 后不保留按历史会话增长的 lifecycle 映射。"""

    binding = hub.create(cc_req(workdir))
    sid = binding.session_id
    hub._publish_agent_event(
        sid,
        {
            "schema": "agent-event-v1",
            "session_id": sid,
            "runtime": "claude_code",
            "seq": 1,
            "type": "turn_start",
            "thread_id": None,
            "turn_id": "turn-before-close",
            "item_id": None,
            "payload": {},
        },
    )

    assert await hub.delete(sid) is True

    assert sid not in hub._session_state_generations
    assert sid not in hub._current_root_turn_ids
    assert sid not in hub._last_root_turn_states
    assert sid not in hub._last_event_sequences
    assert sid not in hub._closing_session_ids


async def _create_binding(hub: SessionHub, workdir: Path):
    """为幂等创建测试返回一个异步创建结果。"""

    return hub.create(cc_req(workdir))


async def test_close_result_reports_not_found_without_raising(hub: SessionHub) -> None:
    """显式关闭结果应区分会话本来就不存在。"""

    result = await hub.close_result("nope")

    assert result.status == "not_found"
    assert result.remaining_resource_count == 0


@pytest.mark.parametrize("first_deletes_binding", [True, False])
async def test_concurrent_close_reuses_one_task_and_keeps_closed_result(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    monkeypatch: pytest.MonkeyPatch,
    first_deletes_binding: bool,
) -> None:
    """用户关闭与应用 drain 无论谁先到，都只清一次 runtime 并最终删除 binding。"""

    binding = hub.create(cc_req(workdir))
    host = cc_registry[binding.session_id]
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    close_calls = 0

    async def slow_close() -> None:
        nonlocal close_calls
        close_calls += 1
        close_started.set()
        await allow_close.wait()
        host.closed = True

    monkeypatch.setattr(host, "close", slow_close)
    first = asyncio.create_task(
        hub.close_result(
            binding.session_id,
            delete_binding=first_deletes_binding,
        )
    )
    await close_started.wait()
    second = asyncio.create_task(
        hub.close_result(
            binding.session_id,
            delete_binding=not first_deletes_binding,
        )
    )
    await asyncio.sleep(0)

    allow_close.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert hub.get(binding.session_id) is None
    retry_result = await hub.close_result(binding.session_id)

    assert first_result == second_result == retry_result
    assert first_result.status == "closed"
    assert close_calls == 1


async def test_close_keeps_binding_when_registry_still_has_session_resource(
    tmp_path: Path,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    name_counts: dict[str, int],
) -> None:
    """runtime 自报完成不能越过资源账本中的未关闭 handle。"""

    observations = []
    registry = ResourceRegistry(
        app_instance_id="test-instance",
        owner_close_observer=observations.append,
    )
    store = BindingStore(tmp_path / "resource-bindings.json")
    hub = SessionHub(
        store,
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
        codex_config_home=tmp_path,
        resource_registry=registry,
    )
    binding = hub.create(codex_req(workdir))
    registry.register_handle(
        resource_id="unclosed-watcher",
        owner_scope=OwnerScope.SESSION,
        resource_kind="workflow_watcher",
        agent_session_id=binding.session_id,
    )

    result = await hub.close_result(binding.session_id)

    assert result.status == "needs_reconcile"
    assert result.remaining_resource_count == 1
    assert result.remaining_resource_kinds == ("workflow_watcher",)
    assert len(observations) == 1
    assert observations[0].status == "needs_reconcile"
    assert observations[0].remaining_resource_count == 1
    assert store.get(binding.session_id) == binding
    with pytest.raises(SessionConflictError, match="正在关闭"):
        _ = [event async for event in hub.stream(binding.session_id, "too late")]


async def test_close_records_cc_session_owner_after_runtime_reaches_zero(
    tmp_path: Path,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    name_counts: dict[str, int],
) -> None:
    """CC 没有 manager 内部回调时，统一 session 生命周期仍提交成功终态。"""

    observations = []
    registry = ResourceRegistry(
        app_instance_id="test-instance",
        owner_close_observer=observations.append,
    )
    hub = SessionHub(
        BindingStore(tmp_path / "closed-owner-bindings.json"),
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
        codex_config_home=tmp_path,
        resource_registry=registry,
    )
    binding = hub.create(cc_req(workdir))

    result = await hub.close_result(binding.session_id)

    assert result.status == "closed"
    assert len(observations) == 1
    assert observations[0].owner_scope is OwnerScope.SESSION
    assert observations[0].status == "closed"


async def test_close_exception_records_session_owner_needs_reconcile(
    tmp_path: Path,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    name_counts: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime 意外抛错时仍应留下 owner 失败事实和可重试 binding。"""

    observations = []
    registry = ResourceRegistry(
        app_instance_id="test-instance",
        owner_close_observer=observations.append,
    )
    store = BindingStore(tmp_path / "failed-owner-bindings.json")
    hub = SessionHub(
        store,
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
        codex_config_home=tmp_path,
        resource_registry=registry,
    )
    binding = hub.create(codex_req(workdir))
    registry.register_handle(
        resource_id="unclosed-watcher",
        owner_scope=OwnerScope.SESSION,
        resource_kind="workflow_watcher",
        agent_session_id=binding.session_id,
    )

    async def fail_close(*_args, **_kwargs) -> None:
        raise OSError("simulated runtime transport failure")

    monkeypatch.setattr(codex_mgr, "close_session", fail_close)

    with pytest.raises(OSError, match="simulated runtime transport failure"):
        await hub.close_result(binding.session_id)

    assert len(observations) == 1
    assert observations[0].owner_scope is OwnerScope.SESSION
    assert observations[0].status == "needs_reconcile"
    assert observations[0].remaining_resource_count == 1
    assert registry.get("unclosed-watcher").state.value == "needs_reconcile"
    assert store.get(binding.session_id) == binding


@pytest.mark.parametrize("request_factory", [cc_req, codex_req])
async def test_delete_user_session_persists_review_before_dropping_binding(
    tmp_path: Path,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    name_counts: dict[str, int],
    request_factory,
) -> None:
    requested = []
    store = BindingStore(tmp_path / "review-bindings.json")
    hub = SessionHub(
        store,
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
        codex_config_home=tmp_path,
        session_review_requester=requested.append,
    )
    binding = hub.create(request_factory(workdir))

    assert await hub.delete(binding.session_id) is True

    assert requested == [binding]
    assert store.get(binding.session_id) is None


async def test_delete_internal_or_memory_disabled_session_does_not_request_review(
    tmp_path: Path,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    name_counts: dict[str, int],
) -> None:
    requested = []
    hub = SessionHub(
        BindingStore(tmp_path / "review-bindings.json"),
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
        codex_config_home=tmp_path,
        session_review_requester=requested.append,
    )
    delegate = hub.create(cc_req(workdir, session_kind="delegate"))
    memory_off = hub.create(codex_req(workdir, memory_enabled=False))

    await hub.delete(delegate.session_id)
    await hub.delete(memory_off.session_id)

    assert requested == []


async def test_review_enqueue_failure_keeps_binding_retryable(
    tmp_path: Path,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
    codex_mgr: FakeCodexManager,
    name_counts: dict[str, int],
) -> None:
    def fail_request(_binding) -> None:
        raise OSError("sessions database unavailable")

    store = BindingStore(tmp_path / "review-bindings.json")
    hub = SessionHub(
        store,
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
        codex_config_home=tmp_path,
        session_review_requester=fail_request,
    )
    binding = hub.create(cc_req(workdir))

    with pytest.raises(OSError, match="sessions database unavailable"):
        await hub.delete(binding.session_id)

    assert store.get(binding.session_id) == binding


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


def test_restart_does_not_let_disconnected_history_occupy_name(
    hub: SessionHub,
    workdir: Path,
) -> None:
    historical = hub.create(cc_req(workdir))

    restarted = SessionHub(
        BindingStore(hub._store.path),
        codex_manager=FakeCodexManager(),
        cc_registry={},
        cc_opener=make_cc_opener({}, {}),
        codex_config_home=workdir.parent,
    )
    new_session = restarted.create(codex_req(workdir))

    assert historical.name == workdir.name
    assert new_session.name == workdir.name


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
