from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from trowel_py.agent_host.capacity import CapacityLimits
from trowel_py.agent_host.hub import SessionConflictError, SessionHub
from trowel_py.agent_host.lifecycle import SessionReconcileRequiredError
from trowel_py.agent_host.store import BindingStore
from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    cc_req,
    codex_req,
    make_cc_opener,
)


class _BlockingCcHost(FakeCcHost):
    """保持 CC 启动窗口，直到测试显式允许进入运行态。"""

    def __init__(
        self,
        workdir: str,
        *,
        started: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        super().__init__(workdir)
        self._started = started
        self._release = release

    async def send(self, text: str) -> AsyncIterator[dict[str, Any]]:
        """暴露运行时尚未标记在跑的准入竞态窗口。"""

        self._started.set()
        await self._release.wait()
        self.running = True
        try:
            yield {"type": "text", "text": text}
        finally:
            self.running = False


class _FailingCcHost(FakeCcHost):
    """在运行时接受输入前失败。"""

    async def send(self, text: str) -> AsyncIterator[dict[str, Any]]:
        """模拟没有形成活动轮次的启动失败。"""

        del text
        if False:
            yield {}
        raise RuntimeError("turn start failed")


class _UncloseableCcHost(FakeCcHost):
    """模拟无法确认子进程已经关闭的 CC host。"""

    async def close(self) -> None:
        """让关闭失败并保留原有 registry。"""

        raise RuntimeError("close failed")


def _limited_hub(
    factory: Callable[[CapacityLimits | None], SessionHub],
    *,
    user_connections: int = 20,
    delegate_connections: int = 5,
    delegate_running: int = 5,
) -> SessionHub:
    """构造使用测试指定资源上限的 Session Hub。"""

    return factory(
        CapacityLimits(
            user_connections=user_connections,
            delegate_connections=delegate_connections,
            delegate_running=delegate_running,
        )
    )


def test_user_and_delegate_connection_pools_do_not_consume_each_other(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
) -> None:
    hub = _limited_hub(
        hub_factory,
        user_connections=1,
        delegate_connections=1,
    )

    hub.create(cc_req(workdir))
    hub.create(codex_req(workdir, session_kind="delegate"))

    with pytest.raises(SessionConflictError, match="连接数已达上限"):
        hub.create(codex_req(workdir))
    with pytest.raises(SessionConflictError, match="当前委派数量已满：连接上限为 1"):
        hub.create(cc_req(workdir, session_kind="delegate"))


def test_delegate_connection_pool_is_shared_by_both_runtimes(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
) -> None:
    hub = _limited_hub(hub_factory, delegate_connections=1)

    hub.create(cc_req(workdir, session_kind="delegate"))

    with pytest.raises(SessionConflictError, match="当前委派数量已满：连接上限为 1"):
        hub.create(codex_req(workdir, session_kind="delegate"))


@pytest.mark.anyio
async def test_delegate_connection_slot_releases_only_after_confirmed_delete(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    hub = _limited_hub(hub_factory, delegate_connections=1)
    delegate = hub.create(cc_req(workdir, session_kind="delegate"))
    cc_registry[delegate.session_id] = _UncloseableCcHost(str(workdir))

    with pytest.raises(SessionReconcileRequiredError, match="needs reconciliation"):
        await hub.delete(delegate.session_id)
    with pytest.raises(SessionConflictError, match="当前委派数量已满"):
        hub.create(codex_req(workdir, session_kind="delegate"))

    cc_registry[delegate.session_id] = FakeCcHost(str(workdir))
    assert await hub.delete(delegate.session_id) is True
    hub.create(codex_req(workdir, session_kind="delegate"))


def test_delegate_connection_admission_is_atomic_across_threads(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    registry: dict[str, FakeCcHost] = {}
    names: dict[str, int] = {}
    ordinary_opener = make_cc_opener(registry, names)

    def slow_opener(req, target_registry, **launch_config):
        time.sleep(0.03)
        return ordinary_opener(req, target_registry, **launch_config)

    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        cc_registry=registry,
        cc_opener=slow_opener,
        capacity_limits=CapacityLimits(
            user_connections=20,
            delegate_connections=1,
            delegate_running=5,
        ),
    )
    start = threading.Barrier(3)

    def create_delegate() -> str:
        start.wait()
        return hub.create(cc_req(workdir, session_kind="delegate")).session_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_delegate) for _ in range(2)]
        start.wait()
        results: list[str] = []
        errors: list[BaseException] = []
        for future in futures:
            try:
                results.append(future.result())
            except BaseException as exc:
                errors.append(exc)

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], SessionConflictError)
    assert "当前委派数量已满：连接上限为 1" in str(errors[0])


@pytest.mark.anyio
async def test_delegate_running_admission_is_atomic_and_releases_after_turn(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    hub = _limited_hub(
        hub_factory,
        delegate_connections=2,
        delegate_running=1,
    )
    first = hub.create(cc_req(workdir, session_kind="delegate"))
    second = hub.create(cc_req(workdir, session_kind="delegate"))
    started = asyncio.Event()
    release = asyncio.Event()
    cc_registry[first.session_id] = _BlockingCcHost(
        str(workdir),
        started=started,
        release=release,
    )

    first_turn = asyncio.create_task(_consume(hub.stream(first.session_id, "first")))
    await started.wait()

    with pytest.raises(
        SessionConflictError,
        match="当前委派数量已满：同时在跑上限为 1",
    ):
        await _consume(hub.stream(second.session_id, "second"))

    release.set()
    await first_turn
    assert await _consume(hub.stream(second.session_id, "second")) != []


@pytest.mark.anyio
async def test_delegate_running_pool_is_shared_by_both_runtimes(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    hub = _limited_hub(
        hub_factory,
        delegate_connections=2,
        delegate_running=1,
    )
    cc = hub.create(cc_req(workdir, session_kind="delegate"))
    codex = hub.create(codex_req(workdir, session_kind="delegate"))
    started = asyncio.Event()
    release = asyncio.Event()
    cc_registry[cc.session_id] = _BlockingCcHost(
        str(workdir),
        started=started,
        release=release,
    )
    cc_turn = asyncio.create_task(_consume(hub.stream(cc.session_id, "first")))
    await started.wait()

    with pytest.raises(
        SessionConflictError,
        match="当前委派数量已满：同时在跑上限为 1",
    ):
        await hub.start_codex_turn(codex.session_id, "second")

    release.set()
    await cc_turn


@pytest.mark.anyio
async def test_failed_turn_start_releases_delegate_running_reservation(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    hub = _limited_hub(
        hub_factory,
        delegate_connections=2,
        delegate_running=1,
    )
    failed = hub.create(cc_req(workdir, session_kind="delegate"))
    next_delegate = hub.create(cc_req(workdir, session_kind="delegate"))
    cc_registry[failed.session_id] = _FailingCcHost(str(workdir))

    with pytest.raises(RuntimeError, match="turn start failed"):
        await _consume(hub.stream(failed.session_id, "fail"))

    assert await _consume(hub.stream(next_delegate.session_id, "after failure")) != []


@pytest.mark.anyio
async def test_interrupt_ack_does_not_release_delegate_running_slot(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    hub = _limited_hub(
        hub_factory,
        delegate_connections=2,
        delegate_running=1,
    )
    running = hub.create(codex_req(workdir, session_kind="delegate"))
    waiting = hub.create(cc_req(workdir, session_kind="delegate"))
    await hub.start_codex_turn(running.session_id, "work")

    await hub.interrupt(running.session_id)

    with pytest.raises(SessionConflictError, match="同时在跑上限为 1"):
        await _consume(hub.stream(waiting.session_id, "too early"))

    session = codex_mgr.get_session(running.session_id)
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.INTERRUPTED,
            thread_id=session.binding.thread_id,
            turn_id="fake-turn-id",
            payload=immutable_payload(status="interrupted"),
        )
    )
    assert await _consume(hub.stream(waiting.session_id, "after terminal")) != []


@pytest.mark.anyio
async def test_running_codex_delegate_is_interrupted_and_archived_when_deleted(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    delegate = hub.create(codex_req(workdir, session_kind="delegate"))
    await hub.start_codex_turn(delegate.session_id, "work")

    assert await hub.delete(delegate.session_id) is True
    assert codex_mgr.close_calls == [(delegate.session_id, False)]
    assert codex_mgr.get_session(delegate.session_id) is None


@pytest.mark.anyio
async def test_codex_delegate_cannot_start_turn_after_delete_begins(
    hub: SessionHub,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegate = hub.create(codex_req(workdir, session_kind="delegate"))
    runtime = hub._runtime_ports[delegate.runtime]
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    original_close = runtime.close

    async def blocking_close(binding) -> Any:
        close_started.set()
        await allow_close.wait()
        return await original_close(binding)

    monkeypatch.setattr(runtime, "close", blocking_close)

    deleting = asyncio.create_task(hub.delete(delegate.session_id))
    await close_started.wait()
    with pytest.raises(SessionConflictError, match="正在关闭"):
        await hub.start_codex_turn(delegate.session_id, "must not start")

    allow_close.set()
    assert await deleting is True


async def _consume(stream: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    """读完一个 Hub 事件流并返回全部事件。"""

    return [event async for event in stream]
