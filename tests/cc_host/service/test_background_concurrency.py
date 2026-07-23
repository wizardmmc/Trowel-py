from __future__ import annotations

import asyncio
from pathlib import Path

from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import ErrorEvent, FinishedEvent, TextEvent

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
)


# 同一逻辑 turn 只能有一个 stdout reader；活动 send 和断连 drain 共用这条门禁。
class TestBackgroundTaskLogicalTurn:
    async def test_second_sendtext_rejected_while_turn_active(self, tmp_path: Path):
        proc = FakeProc([line(init_event())], feed_eof=False)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        task1 = asyncio.create_task(collect(host.send("first")))
        await asyncio.sleep(0.15)
        assert host.running is True, "第一轮 send 应在跑"
        written_before = list(proc.stdin.written)
        try:
            events2 = await asyncio.wait_for(collect(host.send("second")), timeout=2.0)
        except asyncio.TimeoutError:
            events2 = []
        errors = [e for e in events2 if isinstance(e, ErrorEvent)]
        assert any(getattr(e, "subclass", "") == "turn_in_progress" for e in errors), (
            "上一轮在跑时第二次 SendText 必须 yield turn_in_progress error"
        )
        assert proc.stdin.written == written_before, (
            "拒绝的 send 不能写 stdin（否则第二条用户消息串入上一轮）"
        )
        proc.stdout.feed_eof()
        task1.cancel()
        try:
            await task1
        except (asyncio.CancelledError, Exception):
            pass

    async def test_two_concurrent_tasks_end_only_at_final_result(self, tmp_path: Path):
        # 重复 progress 和 terminal notification 必须幂等，最后一个任务结束后才收口。
        proc = FakeProc(
            [
                line(init_event(sid="s-two")),
                line(
                    {
                        "type": "system",
                        "subtype": "task_started",
                        "task_id": "A",
                        "tool_use_id": "call_a",
                        "task_type": "local_bash",
                    }
                ),
                line(
                    {
                        "type": "system",
                        "subtype": "task_started",
                        "task_id": "B",
                        "tool_use_id": "call_b",
                        "task_type": "local_bash",
                    }
                ),
                line(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "total_cost_usd": 0.01,
                        "usage": {"input_tokens": 10},
                        "num_turns": 1,
                        "duration_ms": 1000,
                    }
                ),
                line(
                    {
                        "type": "system",
                        "subtype": "task_progress",
                        "task_id": "A",
                        "tool_use_id": "call_a",
                        "last_tool_name": "Bash",
                    }
                ),
                line(
                    {
                        "type": "system",
                        "subtype": "task_progress",
                        "task_id": "A",
                        "tool_use_id": "call_a",
                        "last_tool_name": "Bash",
                    }
                ),
                line(
                    {
                        "type": "system",
                        "subtype": "task_notification",
                        "task_id": "A",
                        "tool_use_id": "call_a",
                        "status": "completed",
                        "usage": {},
                    }
                ),
                line(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "total_cost_usd": 0.015,
                        "usage": {"input_tokens": 15},
                        "num_turns": 1,
                        "duration_ms": 1500,
                    }
                ),
                line(
                    {
                        "type": "system",
                        "subtype": "task_notification",
                        "task_id": "A",
                        "tool_use_id": "call_a",
                        "status": "completed",
                        "usage": {},
                    }
                ),
                line(
                    {
                        "type": "system",
                        "subtype": "task_notification",
                        "task_id": "B",
                        "tool_use_id": "call_b",
                        "status": "completed",
                        "usage": {},
                    }
                ),
                line(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "both done"}]},
                    }
                ),
                line(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "total_cost_usd": 0.02,
                        "usage": {"input_tokens": 20},
                        "num_turns": 2,
                        "duration_ms": 2000,
                    }
                ),
            ]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("run A and B"))
        finished = [e for e in events if isinstance(e, FinishedEvent)]
        assert len(finished) == 1, (
            "双任务只在最后一个终止 + 最终 result 后结束，一个 finished"
        )
        assert finished[0].num_turns == 2
        text = "".join(e.text for e in events if isinstance(e, TextEvent))
        assert "both done" in text

    async def test_disconnect_drain_reads_to_logical_terminal(self, tmp_path: Path):
        # SSE 断开只移交读取权；drain 仍须读到 pending 清空后的最终 result。
        proc = FakeProc(
            [
                line(init_event(sid="s-drain")),
                line(
                    {
                        "type": "system",
                        "subtype": "task_started",
                        "task_id": "bg1",
                        "tool_use_id": "call_bg",
                        "task_type": "local_bash",
                    }
                ),
                line(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "total_cost_usd": 0.01,
                        "usage": {},
                        "num_turns": 1,
                        "duration_ms": 1,
                    }
                ),
            ],
            feed_eof=False,
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))

        async def consume() -> None:
            async for _ in host.send("run bg"):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)
        assert host._drain_task is not None, "cancel 后应起后台 drain"
        assert host.running is True, "drain 期间 running=True (slice §6)"
        for ev in [
            {
                "type": "system",
                "subtype": "task_notification",
                "task_id": "bg1",
                "tool_use_id": "call_bg",
                "status": "completed",
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "total_cost_usd": 0.02,
                "usage": {},
                "num_turns": 2,
                "duration_ms": 2,
            },
        ]:
            proc.stdout.feed_data((line(ev) + "\n").encode())
        proc.stdout.feed_eof()
        if host._drain_task is not None:
            await asyncio.wait_for(host._drain_task, timeout=2.0)
        assert host._drain_task is None, "drain 应在逻辑终态结束"
        assert host.running is False
        assert proc.returncode is None, "drain 绝不杀 cc (slice §6)"

    async def test_drain_period_rejects_new_sendtext(self, tmp_path: Path):
        proc = FakeProc(
            [
                line(init_event(sid="s-c1")),
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
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        task1 = asyncio.create_task(collect(host.send("first")))
        await asyncio.sleep(0.15)
        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)
        assert host._drain_task is not None
        assert host.running is True, "drain 期间 running 必须为 True"
        written_before = list(proc.stdin.written)
        events2 = await collect(host.send("second"))
        errors = [e for e in events2 if isinstance(e, ErrorEvent)]
        assert any(getattr(e, "subclass", "") == "turn_in_progress" for e in errors), (
            "drain 期间新 SendText 必须被门禁拒（不抢 pipe / 不抢先 reset）"
        )
        assert proc.stdin.written == written_before, "拒绝的 send 不能写 stdin"
        proc.stdout.feed_eof()
        if host._drain_task is not None:
            try:
                await asyncio.wait_for(host._drain_task, timeout=2.0)
            except (asyncio.CancelledError, Exception):
                pass
