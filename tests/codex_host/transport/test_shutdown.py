from __future__ import annotations

import asyncio

import pytest

from trowel_py.codex_host.errors import TransportClosedError
from trowel_py.codex_host import AppServerClient
from trowel_py.codex_host.version import CodexVersion
from trowel_py.resource_lifecycle import ProcessIdentity
from tests.codex_host._fake import FakeAppServer
from tests.codex_host._fake import Step
from tests.codex_host.transport.support import (
    _build,
    _handshake,
    _initialize_response,
)

# 这些断言会检查 transport 的私有任务状态，以验证内部清理不变量。


async def test_eof_fails_pending_with_host_exited() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.exit(0)

    client, _fake = _build(behavior())
    await client.start()

    async def hang() -> None:
        await client.request("hangs/forever", {}, timeout=5)

    task = asyncio.create_task(hang())
    await asyncio.sleep(0.05)
    with pytest.raises(TransportClosedError):
        await task
    assert client.closed
    await client.close()


async def test_nonzero_exit_records_exit_code() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.exit(1)

    client, _fake = _build(behavior())
    await client.start()

    async def hang() -> None:
        await client.request("hangs/forever", {}, timeout=5)

    task = asyncio.create_task(hang())
    await asyncio.sleep(0.05)
    with pytest.raises(TransportClosedError) as exc:
        await task
    assert exc.value.exit_code == 1
    await client.close()


async def test_close_is_idempotent() -> None:
    client, _fake = _build(_handshake())
    await client.start()
    await client.close()
    await client.close()
    assert client._reader_task is None
    assert client._stderr_task is None
    assert client._process is None


async def test_close_escalates_when_process_ignores_stdin_close() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        await (yield Step.hold(10))

    client, fake = _build(behavior(), close_grace_s=0.05, close_term_s=0.05)
    await client.start()
    await client.close()
    assert fake._process is not None
    assert fake._process.returncode in {-9, -15}


async def test_request_after_close_raises() -> None:
    client, _fake = _build(_handshake())
    await client.start()
    await client.close()
    with pytest.raises(TransportClosedError):
        await client.request("anything", {})


async def test_no_orphan_tasks_after_close() -> None:
    client, _fake = _build(_handshake())
    await client.start()
    await asyncio.sleep(0.02)
    await client.close()
    leaked = [
        t
        for t in asyncio.all_tasks()
        if t.get_name()
        in {"codex-reader", "codex-stderr", "fake-app-server", "codex-server-request"}
    ]
    assert leaked == []


async def test_server_request_handler_task_is_cancelled_on_close() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 99,
                "params": {"command": "x"},
            }
        )
        yield Step.recv()

    client, _fake = _build(behavior())

    started = asyncio.Event()

    async def slow_handler(_request_id: object, _method: str, _params: dict) -> dict:
        started.set()
        await asyncio.Event().wait()
        return {"decision": "accept"}

    client.register_server_request_handler(
        "item/commandExecution/requestApproval", slow_handler
    )
    await client.start()
    await started.wait()
    await client.close()
    handler_tasks = [
        t for t in asyncio.all_tasks() if t.get_name() == "codex-server-request"
    ]
    assert handler_tasks == []


async def test_close_signals_verified_app_server_process_group() -> None:
    """app-server 不响应 stdin EOF 时必须升级终止整个独立进程组。"""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        await (yield Step.hold(10))

    class Controller:
        """把进程组信号映射到 fake 根进程并记录调用。"""

        def __init__(self) -> None:
            self.process = None
            self.signals: list[tuple[int, str]] = []

        def inspect(self, pid: int) -> ProcessIdentity | None:
            return ProcessIdentity(pid=pid, process_group=812, start_identity="start")

        def group_alive(self, process_group: int) -> bool:
            return bool(
                process_group == 812
                and self.process is not None
                and self.process.returncode is None
            )

        def signal_group(self, process_group: int, signal_name: str) -> None:
            self.signals.append((process_group, signal_name))
            assert self.process is not None
            if signal_name == "SIGTERM":
                self.process.terminate()
            elif signal_name == "SIGKILL":
                self.process.kill()

    async def version_reader() -> CodexVersion:
        return CodexVersion("codex-cli 0.144.0", (0, 144, 0))

    fake = FakeAppServer(behavior())
    base_spawner = fake.spawner()
    controller = Controller()

    async def spawner(args, kwargs):
        process = await base_spawner(args, kwargs)
        process.pid = 811
        controller.process = process
        return process

    client = AppServerClient(
        expected_version="0.144.0",
        version_reader=version_reader,
        spawner=spawner,
        process_controller=controller,
        close_grace_s=0.01,
        close_term_s=0.01,
    )
    await client.start()

    await client.close()

    assert controller.signals == [(812, "SIGTERM")]
