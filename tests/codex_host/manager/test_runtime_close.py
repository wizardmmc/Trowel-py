"""验证 Codex session 关闭会收敛原生 thread，同时保留用户历史。"""

from __future__ import annotations

import asyncio

import pytest

from trowel_py.codex_host import CodexHostManager, CodexSession
from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.resource_lifecycle import OwnerScope, ProcessIdentity, ResourceRegistry
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import (
    _cfg,
    _init_resp,
    _manager,
    _thread_result,
)


async def test_close_idle_user_thread_archives_then_restores_history() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        msg = yield Step.recv()
        assert msg["method"] == "thread/archive"
        yield Step.send({"id": msg["id"], "result": {}})
        msg = yield Step.recv()
        assert msg["method"] == "thread/unarchive"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "done")
    await asyncio.sleep(0.02)

    await manager.close_session(
        session,
        preserve_history=True,
        terminal_timeout_s=0.05,
    )

    methods = [item["method"] for item in fake.received]
    assert methods.index("thread/archive") < methods.index("thread/unarchive")
    assert "thread/resume" not in methods
    await manager.close()


async def test_close_user_thread_already_archived_restores_history() -> None:
    """重试遇到已归档用户 thread 时先恢复历史，不能硬删除 rollout。"""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        msg = yield Step.recv()
        assert msg["method"] == "thread/archive"
        yield Step.send(
            {
                "id": msg["id"],
                "error": {
                    "code": -32600,
                    "message": "no rollout found for thread id t-1",
                },
            }
        )
        msg = yield Step.recv()
        assert msg["method"] == "thread/unarchive"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "done")
    await asyncio.sleep(0.02)

    await manager.close_session(
        session,
        preserve_history=True,
        terminal_timeout_s=0.05,
    )

    methods = [item["method"] for item in fake.received]
    assert methods.index("thread/archive") < methods.index("thread/unarchive")
    assert "thread/delete" not in methods
    await manager.close()


async def test_close_missing_archived_rollout_keeps_user_binding_retryable() -> None:
    """用户历史无法恢复时必须报错，不能把关闭伪装成成功。"""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "interrupted"},
                },
            }
        )
        msg = yield Step.recv()
        assert msg["method"] == "thread/archive"
        yield Step.send({"id": msg["id"], "result": {}})
        msg = yield Step.recv()
        assert msg["method"] == "thread/unarchive"
        yield Step.send(
            {
                "id": msg["id"],
                "error": {
                    "code": -32600,
                    "message": "no archived rollout found for thread id t-1",
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "not persisted")
    await asyncio.sleep(0.02)

    with pytest.raises(ProtocolViolationError, match="no archived rollout found"):
        await manager.close_session(
            session,
            preserve_history=True,
            terminal_timeout_s=0.05,
        )

    methods = [item["method"] for item in fake.received]
    assert methods.index("thread/archive") < methods.index("thread/unarchive")
    assert "thread/delete" not in methods
    await manager.close()


async def test_close_retry_restores_history_before_resource_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """资源核验首次失败时也要先恢复历史，使下一次关闭可以安全重试。"""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        for _ in range(2):
            msg = yield Step.recv()
            assert msg["method"] == "thread/archive"
            yield Step.send({"id": msg["id"], "result": {}})
            msg = yield Step.recv()
            assert msg["method"] == "thread/unarchive"
            yield Step.send({"id": msg["id"], "result": {}})
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(
        fake,
        resource_registry=ResourceRegistry(app_instance_id="test-instance"),
    )
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "done")
    await asyncio.sleep(0.02)
    attempts = 0

    async def reconcile(_session_id: str) -> None:
        """第一次模拟真实进程组仍存活，第二次确认已经归零。"""

        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("process group still alive")

    monkeypatch.setattr(manager, "_reconcile_session_process_groups", reconcile)

    with pytest.raises(RuntimeError, match="still alive"):
        await manager.close_session(
            session,
            preserve_history=True,
            terminal_timeout_s=0.05,
        )
    await manager.close_session(
        session,
        preserve_history=True,
        terminal_timeout_s=0.05,
    )

    methods = [item["method"] for item in fake.received]
    assert methods.count("thread/archive") == 2
    assert methods.count("thread/unarchive") == 2
    assert "thread/delete" not in methods
    await manager.close()


async def test_close_rechecks_resources_after_restoring_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """unarchive 后必须再次核验，最后才能把 session owner 标成 closed。"""

    events: list[str] = []

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        msg = yield Step.recv()
        assert msg["method"] == "thread/archive"
        yield Step.send({"id": msg["id"], "result": {}})
        msg = yield Step.recv()
        assert msg["method"] == "thread/unarchive"
        events.append("unarchive")
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.recv()

    registry = ResourceRegistry(app_instance_id="test-instance")
    original_reconcile = registry.reconcile_process_groups
    original_mark_closed = registry.mark_owner_closed

    def record_reconcile(**kwargs):
        """记录资源核验发生的顺序。"""

        events.append("reconcile")
        return original_reconcile(**kwargs)

    def record_mark_closed(*args, **kwargs) -> None:
        """记录 owner 最终提交发生的顺序。"""

        events.append("closed")
        original_mark_closed(*args, **kwargs)

    monkeypatch.setattr(registry, "reconcile_process_groups", record_reconcile)
    monkeypatch.setattr(registry, "mark_owner_closed", record_mark_closed)
    fake = FakeAppServer(behavior())
    manager = _manager(fake, resource_registry=registry)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "done")
    await asyncio.sleep(0.02)

    await manager.close_session(
        session,
        preserve_history=True,
        terminal_timeout_s=0.05,
    )

    assert events == ["unarchive", "reconcile", "closed"]
    await manager.close()


async def test_close_active_internal_thread_interrupts_then_keeps_archived() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        msg = yield Step.recv()
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "interrupted"},
                },
            }
        )
        msg = yield Step.recv()
        assert msg["method"] == "thread/archive"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "long task")

    await manager.close_session(
        session,
        preserve_history=False,
        terminal_timeout_s=0.05,
    )

    methods = [item["method"] for item in fake.received]
    assert methods.index("turn/interrupt") < methods.index("thread/archive")
    assert "thread/unarchive" not in methods
    await manager.close()


async def test_close_continues_to_archive_when_terminal_does_not_arrive() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        msg = yield Step.recv()
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})
        msg = yield Step.recv()
        assert msg["method"] == "thread/archive"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "long task")

    await manager.close_session(
        session,
        preserve_history=False,
        terminal_timeout_s=0.01,
    )

    methods = [item["method"] for item in fake.received]
    assert methods.index("turn/interrupt") < methods.index("thread/archive")
    await manager.close()


async def test_archive_closes_registered_thread_and_turn_resources() -> None:
    """原生 archive 成功后，session 账本必须确认 thread 与 turn 均归零。"""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        msg = yield Step.recv()
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})
        msg = yield Step.recv()
        assert msg["method"] == "thread/archive"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.recv()

    registry = ResourceRegistry(app_instance_id="test-instance")
    fake = FakeAppServer(behavior())
    manager = _manager(fake, resource_registry=registry)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "long task")

    before = registry.owner_summary(
        OwnerScope.SESSION,
        agent_session_id="s1",
    )
    assert before.live_resource_count == 2
    assert set(before.remaining_resource_kinds) == {
        "codex_thread_resources",
        "codex_turn",
    }

    await manager.close_session(
        session,
        preserve_history=False,
        terminal_timeout_s=0,
    )

    after = registry.owner_summary(
        OwnerScope.SESSION,
        agent_session_id="s1",
    )
    assert after.live_resource_count == 0
    await manager.close()


async def test_inventory_registers_only_independent_descendant_process_groups() -> None:
    """Codex 的 MCP、命令等独立后代应进入连接级 Host 快照。"""

    identities = {
        100: ProcessIdentity(100, 100, "root-start"),
        101: ProcessIdentity(101, 100, "same-group-child"),
        102: ProcessIdentity(102, 102, "mcp-start"),
        103: ProcessIdentity(103, 103, "command-start"),
    }

    class Controller:
        """向账本提供固定的进程身份。"""

        def inspect(self, pid: int) -> ProcessIdentity | None:
            """读取预置进程身份。"""

            return identities.get(pid)

        def group_alive(self, process_group: int) -> bool:
            """预置进程组均视为存活。"""

            return process_group in {100, 102, 103}

        def signal_group(self, process_group: int, signal_name: str) -> None:
            """盘点测试不应发送进程信号。"""

            raise AssertionError((process_group, signal_name))

    class Client:
        """只公开 app-server PID 的最小 client。"""

        pid = 100

    registry = ResourceRegistry(
        app_instance_id="test-instance",
        process_controller=Controller(),
    )
    manager = CodexHostManager(
        resource_registry=registry,
        descendant_inventory=lambda _pid: tuple(identities.values())[1:],
    )
    client = Client()
    manager._client = client  # type: ignore[assignment]  # noqa: SLF001
    manager._active_generation = 1  # noqa: SLF001
    manager._register_connection_resource(client, 1)  # type: ignore[arg-type]  # noqa: SLF001

    await manager._inventory_connection_descendants(  # type: ignore[arg-type]  # noqa: SLF001
        client,
        1,
    )

    summary = registry.private_summary()
    assert summary["kinds"] == {
        "codex_app_server_process_group": 1,
        "codex_descendant_process_group": 2,
    }
