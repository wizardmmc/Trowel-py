from __future__ import annotations

import asyncio
from pathlib import Path

from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import ErrorEvent

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
)


class TestBackgroundTaskLogicalTurn:
    async def test_error_result_kills_process_for_generation_isolation(
        self, tmp_path: Path
    ):
        # error result 后只清 tracker 不够，旧进程的晚到事件会污染下一代会话。
        proc = FakeProc(
            [
                line(init_event(sid="s-c3")),
                line(
                    {
                        "type": "result",
                        "subtype": "max_turns",
                        "is_error": True,
                        "errors": ["exceeded"],
                        "total_cost_usd": 0.0,
                        "usage": {},
                        "num_turns": 10,
                    }
                ),
            ]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) >= 1, "error result 必须 yield error terminal"
        assert proc.returncode is not None, (
            "error result 后必须杀进程（finally _sync_kill），实现 generation 隔离"
        )

    async def test_stalled_does_not_fire_while_background_active(self, tmp_path: Path):
        proc = FakeProc(
            [
                line(init_event(sid="s-h2")),
                line(
                    {
                        "type": "system",
                        "subtype": "task_started",
                        "task_id": "bg1",
                        "tool_use_id": "call_bg",
                        "task_type": "local_bash",
                    }
                ),
            ],
            feed_eof=False,
        )
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            stalled_threshold_mild=0.1,
            stalled_threshold_severe=0.2,
            stalled_threshold_kill=0.3,
            stalled_tick=0.05,
        )
        task = asyncio.create_task(collect(host.send("hi")))
        await asyncio.sleep(0.5)
        assert proc.returncode is None, "后台 task 在跑时 stalled 不该 kill 进程"
        proc.stdout.feed_eof()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_proc_exit_without_result_emits_host_error(self, tmp_path: Path):
        proc = FakeProc([line(init_event(sid="s-h6"))], feed_eof=True)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert any(getattr(e, "subclass", "") == "host_error" for e in errors), (
            "proc EOF 无 result 必须发 host_error 保证一轮一终态"
        )
