from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest

from trowel_py.cc_host.service import CCHost
from trowel_py.resource_lifecycle import OwnerScope, ProcessIdentity, ResourceRegistry
from trowel_py.schemas.cc_host import (
    ErrorEvent,
    LocalCommandEvent,
    SessionExitedEvent,
    StalledWarningEvent,
)

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
)


# stream-json 不解释字面量 /exit，host 必须改走 end_session control_request。
class TestExitSession:
    async def test_exit_writes_end_session_control_request(self, tmp_path: Path):
        proc = FakeProc([])
        proc.returncode = 0
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("/exit"))
        written = b"".join(proc.stdin.written).decode()
        assert '"type": "control_request"' in written
        assert '"subtype": "end_session"' in written
        exited = [e for e in events if isinstance(e, SessionExitedEvent)]
        assert len(exited) == 1
        assert exited[0].returncode == 0

    async def test_quit_alias_also_exits(self, tmp_path: Path):
        proc = FakeProc([])
        proc.returncode = 0
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("/quit"))
        written = b"".join(proc.stdin.written).decode()
        assert '"subtype": "end_session"' in written
        assert any(isinstance(e, SessionExitedEvent) for e in events)

    async def test_exit_does_not_send_user_message(self, tmp_path: Path):
        proc = FakeProc([])
        proc.returncode = 0
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("/exit"))
        written = b"".join(proc.stdin.written).decode()
        assert '"type": "user"' not in written
        assert "/exit" not in written

    async def test_exit_fallback_sigkill_when_cc_does_not_die(self, tmp_path: Path):
        proc = FakeProc([])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("/exit"))
        exited = [e for e in events if isinstance(e, SessionExitedEvent)]
        assert len(exited) == 1
        assert exited[0].returncode == -9


class TestLocalAndRestart:
    async def test_init_updates_effective_model(self, tmp_path: Path):
        proc = FakeProc(
            [line(init_event(model="glm-effective")), line(result_ok())]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))

        await collect(host.send("hi"))

        assert host.model is None
        assert host.effective_model == "glm-effective"

    async def test_assistant_model_supersedes_init_model(self, tmp_path: Path):
        assistant = {
            "type": "assistant",
            "message": {
                "model": "glm-actual",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
        proc = FakeProc(
            [
                line(init_event(model="glm-init")),
                line(assistant),
                line(result_ok()),
            ]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))

        await collect(host.send("hi"))

        assert host.effective_model == "glm-actual"

    async def test_cost_does_not_hit_cc(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("hi"))
        proc.stdin.written.clear()
        events = await collect(host.send("/cost"))
        assert any(isinstance(e, LocalCommandEvent) for e in events)
        assert proc.stdin.written == []

    async def test_effort_triggers_restart_next_send(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        proc2 = FakeProc([line(init_event(sid="s-1")), line(result_ok())])
        spawner = FakeSpawner([proc1, proc2])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        await collect(host.send("/effort high"))
        assert host.effort == "high"
        await collect(host.send("again"))
        assert len(spawner.spawned) >= 2
        second_args = spawner.spawned[1][0]
        assert "--resume" in second_args
        assert "s-1" in second_args

    async def test_model_emits_model_changed_for_immediate_sync(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/model opus"))
        assert host.model == "opus"
        assert host.effective_model is None
        types = [e.type for e in events]
        assert "model_changed" in types
        mc = next(e for e in events if e.type == "model_changed")
        assert mc.model == "opus"

    async def test_effort_emits_model_changed_with_effort(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/effort high"))
        mc = next(e for e in events if e.type == "model_changed")
        assert mc.effort == "high"

    async def test_bare_model_does_not_kill_process(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/model"))
        assert len(spawner.spawned) == 1
        assert "model_changed" not in [e.type for e in events]
        assert any(e.type == "local_command" for e in events)

    async def test_model_picked_args_reach_next_spawn(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        proc2 = FakeProc([line(init_event(sid="s-1")), line(result_ok())])
        spawner = FakeSpawner([proc1, proc2])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        await collect(host.send("/model opus"))
        await collect(host.send("again"))
        assert len(spawner.spawned) >= 2
        second_args = spawner.spawned[1][0]
        i = second_args.index("--model")
        assert second_args[i + 1] == "opus"


class TestInterrupt:
    async def test_interrupt_marks_dead_and_next_send_resumes(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        proc2 = FakeProc([line(init_event(sid="s-1")), line(result_ok())])
        spawner = FakeSpawner([proc1, proc2])
        host = CCHost("sid", tmp_path, spawner=spawner)
        host._interrupt_proc = lambda p: setattr(p, "returncode", 0)
        await collect(host.send("hi"))
        await host.interrupt()
        assert host.is_dead is True
        await collect(host.send("next"))
        assert len(spawner.spawned) == 2
        second_args = spawner.spawned[1][0]
        assert "--resume" in second_args and "s-1" in second_args

    async def test_without_controller_interrupts_only_the_root_process(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """兼容路径不能把 SIGINT 发给 host 所在的操作系统进程组。"""

        proc = FakeProc([], feed_eof=False, pid=701)
        group_signals: list[tuple[int, signal.Signals]] = []
        monkeypatch.setattr(os, "getpgid", lambda _pid: 701)
        monkeypatch.setattr(
            os,
            "killpg",
            lambda process_group, signum: group_signals.append(
                (process_group, signum)
            ),
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await host._ensure_process()

        host._interrupt_proc(proc)

        assert proc.signals == [signal.SIGINT]
        assert group_signals == []


class FakeProcessController:
    """让关闭测试观察进程组信号，并由 TERM 结束假进程。"""

    def __init__(self, proc: FakeProc) -> None:
        self.proc = proc
        self.signals: list[tuple[int, str]] = []

    def inspect(self, pid: int) -> ProcessIdentity | None:
        """返回假进程的固定启动身份。"""

        if pid != self.proc.pid or self.proc.returncode is not None:
            return None
        return ProcessIdentity(pid, pid, f"start-{pid}")

    def group_alive(self, process_group: int) -> bool:
        """以假进程返回码判断进程组是否仍活着。"""

        return process_group == self.proc.pid and self.proc.returncode is None

    def signal_group(self, process_group: int, signal_name: str) -> None:
        """记录信号，并让 TERM 或 KILL 结束假进程。"""

        self.signals.append((process_group, signal_name))
        if signal_name == "SIGTERM":
            self.proc.returncode = -15
        elif signal_name == "SIGKILL":
            self.proc.returncode = -9


class TestCloseProcessGroup:
    async def test_close_escalates_the_registered_process_group(
        self,
        tmp_path: Path,
    ) -> None:
        proc = FakeProc([], feed_eof=False, pid=701)
        controller = FakeProcessController(proc)
        registry = ResourceRegistry(
            app_instance_id="app-1",
            snapshot_path=tmp_path / "resources.json",
            process_controller=controller,
        )
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            process_controller=controller,
            resource_registry=registry,
            close_interrupt_s=0,
            close_term_s=0,
            close_kill_s=0,
        )
        await host._ensure_process()

        await host.close()

        assert proc.stdin.closed is True
        assert controller.signals == [(701, "SIGTERM")]
        assert registry.owner_summary(
            owner_scope=OwnerScope.SESSION,
            agent_session_id="sid",
        ).live_resource_count == 0

    async def test_active_close_interrupts_group_before_term(
        self,
        tmp_path: Path,
    ) -> None:
        proc = FakeProc([], feed_eof=False, pid=702)
        controller = FakeProcessController(proc)
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            process_controller=controller,
            close_interrupt_s=0,
            close_term_s=0,
            close_kill_s=0,
        )
        await host._ensure_process()
        host.running = True

        await host.close()

        assert controller.signals == [(702, "SIGINT"), (702, "SIGTERM")]

    async def test_close_tracks_group_after_root_process_has_exited(
        self,
        tmp_path: Path,
    ) -> None:
        """根 PID 退出后仍须按 spawn 时身份清理同组孙进程。"""

        proc = FakeProc([], feed_eof=False, pid=703)
        controller = FakeProcessController(proc)
        descendants_alive = True

        def group_alive(process_group: int) -> bool:
            return process_group == 703 and descendants_alive

        def signal_group(process_group: int, signal_name: str) -> None:
            nonlocal descendants_alive
            controller.signals.append((process_group, signal_name))
            descendants_alive = False

        controller.group_alive = group_alive  # type: ignore[method-assign]
        controller.signal_group = signal_group  # type: ignore[method-assign]
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            process_controller=controller,
            close_term_s=0,
            close_kill_s=0,
        )
        await host._ensure_process()
        proc.returncode = 0

        await host.close()

        assert controller.signals == [(703, "SIGTERM")]

    async def test_sync_kill_refuses_reused_process_identity(
        self,
        tmp_path: Path,
    ) -> None:
        """同步兜底发现 PID 已复用时必须保留诊断且不能向原进程组发信号。"""

        proc = FakeProc([], feed_eof=False, pid=704)
        controller = FakeProcessController(proc)
        registry = ResourceRegistry(
            app_instance_id="app-1",
            process_controller=controller,
        )
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            process_controller=controller,
            resource_registry=registry,
        )
        await host._ensure_process()
        controller.inspect = lambda _pid: ProcessIdentity(  # type: ignore[method-assign]
            704,
            704,
            "replacement-start",
        )

        host._sync_kill()

        assert controller.signals == []
        assert proc.returncode is None
        assert registry.owner_summary(
            OwnerScope.SESSION,
            agent_session_id="sid",
        ).status == "needs_reconcile"


class TestStalled:
    async def test_stalled_emits_phased_warnings_then_kill(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event())], feed_eof=False)
        spawner = FakeSpawner([proc1])
        clock = {"t": 0.0}

        def fake_now() -> float:
            clock["t"] += 150.0
            return clock["t"]

        host = CCHost(
            "sid",
            tmp_path,
            spawner=spawner,
            now=fake_now,
            stalled_threshold_mild=120.0,
            stalled_threshold_severe=300.0,
            stalled_threshold_kill=1800.0,
            stalled_tick=0.001,
        )
        events = await collect(host.send("long job"))
        warnings = [e for e in events if isinstance(e, StalledWarningEvent)]
        severities = [w.severity for w in warnings]
        assert severities.count("mild") == 1
        assert severities.count("severe") == 1
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert any(e.subclass == "stalled" for e in errors)
        assert len(spawner.spawned) == 1


class TestCancellation:
    async def test_cancelled_send_keeps_cc_alive(self, tmp_path: Path):
        proc = FakeProc([], feed_eof=False)
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            stalled_threshold_mild=10000.0,
            stalled_tick=0.01,
        )

        async def _run():
            async for _ in host.send("hi"):
                pass

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert proc.returncode is None

    async def test_drain_task_holds_strong_ref_and_clears(self, tmp_path: Path):
        # event loop 只保留弱引用；host 必须持有 drain task，避免 stdout 无人消费。
        proc = FakeProc([line(init_event())], feed_eof=False)
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            stalled_threshold_mild=10000.0,
            stalled_tick=0.01,
        )

        async def _run():
            async for _ in host.send("hi"):
                pass

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.02)
        assert host._drain_task is not None
        await host.close()
        assert host._drain_task is None
