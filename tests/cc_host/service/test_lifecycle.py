from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trowel_py.cc_host.service import CCHost
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
