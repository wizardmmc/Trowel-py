"""Tests for cc_host.service.CCHost using a fake CC subprocess.

No real `claude` is spawned. A fake process feeds preset stdout lines so we
can assert the translated event stream, the respawn-after-interrupt path, and
the stalled auto-restart path.
"""
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.session_scan import workdir_to_slug
from trowel_py.schemas.cc_host import (
    SessionStartedEvent,
    TextEvent,
    ToolCallEvent,
    FinishedEvent,
    ErrorEvent,
    LocalCommandEvent,
    StalledWarningEvent,
    TurnStartEvent,
    SessionExitedEvent,
)


# ---------------------------------------------------------------------------
# Fake subprocess + spawner (test-only infrastructure)
# ---------------------------------------------------------------------------


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def write_eof(self) -> None:
        return None


class FakeProc:
    """Mimics the asyncio subprocess surface CCHost touches."""

    def __init__(self, lines: list[str], feed_eof: bool = True, pid: int = 1) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = FakeWriter()
        self.stdout = asyncio.StreamReader()
        for ln in lines:
            self.stdout.feed_data(ln.encode() + b"\n")
        if feed_eof:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


class FakeSpawner:
    """Hands out FakeProc instances in order, recording spawn args."""

    def __init__(self, procs: list[FakeProc]) -> None:
        self._procs = list(procs)
        self.spawned: list[tuple[list[str], dict]] = []

    async def __call__(self, args: list[str], kwargs: dict) -> FakeProc:
        proc = self._procs.pop(0)
        self.spawned.append((args, kwargs))
        return proc


# end fakes
# ---------------------------------------------------------------------------


def line(d: dict) -> str:
    return json.dumps(d)


def init_event(sid="s-1", model="glm-5.2"):
    return {"type": "system", "subtype": "init", "model": model,
            "cwd": "/wd", "session_id": sid, " tools": [], "tools": ["Read", "Skill"]}


def text_delta(i, t):
    return {"type": "stream_event", "event": {"type": "content_block_delta",
            "index": i, "delta": {"type": "text_delta", "text": t}}}


def result_ok():
    return {"type": "result", "subtype": "success", "is_error": False,
            "total_cost_usd": 0.03, "usage": {"input_tokens": 5}, "num_turns": 1}


async def collect(gen):
    return [x async for x in gen]


class TestStartAndSend:
    async def test_send_cancel_does_not_kill_cc(self, tmp_path: Path):
        """slice-028 v2 多 session: 前端断开（刷新/切换会话）绝不能杀 cc——
        否则刷新会中断 AI 思考，最后那个 turn 的回复永久丢失（实测 jsonl 末尾
        只剩 user message）。后台 drain 接管读 stdout 到 result，cc 继续跑完。"""
        proc = FakeProc([line(init_event())], feed_eof=False)  # cc 还在跑
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        gen = host.send("hi")

        async def consume() -> None:
            async for _ in gen:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)  # 让 task 启动 + 读 init + 挂在 readline
        task.cancel()  # 模拟前端断开（前端 SSE 断 → StreamingResponse cancel）
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)  # 让后台 drain task 调度
        assert proc.returncode is None, "前端断开不该杀 cc（多 session 模型）"

    async def test_send_yields_session_started_text_finished(self, tmp_path: Path):
        proc = FakeProc([
            line(init_event()),
            line(text_delta(0, "hel")),
            line(text_delta(0, "lo")),
            line(result_ok()),
        ])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        kinds = [type(e).__name__ for e in events]
        # slice-026: a TurnStartEvent is yielded first (before SessionStarted)
        assert kinds[0] == "TurnStartEvent"
        assert SessionStartedEvent in [type(e) for e in events]
        text_ev = [e for e in events if isinstance(e, TextEvent)]
        assert "".join(e.text for e in text_ev) == "hello"
        assert isinstance(events[-1], FinishedEvent)
        assert host.cc_session_id == "s-1"

    async def test_send_survives_overlong_readline_chunk(self, tmp_path: Path):
        """slice-028 bug1: readline 抛 'Separator is found, but chunk is longer
        than limit' 时，service 必须兜底——yield ErrorEvent(subclass=overlong_line)
        + 结束 turn，不让 ValueError 冒泡到 routes.py 变 host_error。drain 超长
        行复杂且易死循环，故选明确报错 + break（防御性，超 16MB 行极罕见）。
        FakeProc.stdout 用默认 StreamReader (64KB limit)，feed 80KB 行触发。"""
        overlong = '{"type":"stream_event","big":"' + ("x" * 80000) + '"}'
        proc = FakeProc([
            line(init_event()),
            overlong,  # 80KB > 64KB default StreamReader limit → ValueError
            line(result_ok()),
        ])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))  # 不抛 = 兜底成功
        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert any(
            getattr(e, "subclass", None) == "overlong_line" for e in error_events
        )

    async def test_send_yields_session_exited_when_cc_exits(self, tmp_path: Path):
        """slice-028 bug3: cc /exit 后（result 事件后 proc 退出），service 要检测
        proc.returncode 并 yield SessionExitedEvent，让前端知道 session 已结束。
        FakeProc.returncode=0 模拟 cc 已退出。"""
        proc = FakeProc([line(init_event()), line(result_ok())])
        proc.returncode = 0  # cc 已退出（/exit）
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        exited = [e for e in events if isinstance(e, SessionExitedEvent)]
        assert len(exited) == 1
        assert exited[0].returncode == 0

    async def test_send_no_session_exited_for_normal_turn(self, tmp_path: Path):
        """slice-028 bug3 反例：普通 turn（cc 不退出，returncode None）不 yield
        SessionExitedEvent。"""
        proc = FakeProc([line(init_event()), line(result_ok())])
        # returncode 保持 None（cc 还活，等下一个输入）
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        exited = [e for e in events if isinstance(e, SessionExitedEvent)]
        assert len(exited) == 0

    async def test_send_writes_user_message_to_stdin(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("ping"))
        written = b"".join(proc.stdin.written).decode()
        assert '"type": "user"' in written
        assert "ping" in written

    async def test_tool_call_streamed(self, tmp_path: Path):
        proc = FakeProc([
            line(init_event()),
            line({"type": "stream_event", "event": {"type": "content_block_start",
                "index": 0, "content_block": {"type": "tool_use", "id": "tu_1", "name": "Write"}}}),
            line({"type": "stream_event", "event": {"type": "content_block_delta",
                "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"p":"/a"}'}}}),
            line({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
            line(result_ok()),
        ])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("write a file"))
        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(calls) == 1
        assert calls[0].tool_name == "Write"


class TestBackgroundTaskLogicalTurn:
    """slice-077-prefix: Bash/Agent 后台任务的第一个 result 只是 cc 的前台回复
    边界，不是 trowel 的逻辑 turn 终态。后台 task_started 注册 pending；
    task_notification terminal 移除 pending；只有 pending 清空 + 无 workflow 在跑
    时的 result 才是终态 finished。

    Ground truth: slice-077-prefix 隔离复现 C（real CC 2.1.197 local_bash 录制）。
    Order: task_started → result(success) → task_updated → task_notification(completed)
    → assistant('DONE') → result(success).
    """

    @staticmethod
    def _bg_local_bash_lines() -> list[str]:
        """Raw stream-json lines: local_bash background task with a mid-turn result.

        Real event shapes from tests/cc_host/test_translator.py sample 030 + docs
        cc-workflow-event-model.md; order from slice-077-prefix 隔离复现 C. Redacted:
        no real paths/keys/tool outputs.
        """
        return [
            line({"type": "system", "subtype": "init", "session_id": "s-bg",
                  "model": "glm-5.2", "cwd": "/wd", "tools": ["Read", "Bash"]}),
            line({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "后台跑着，等通知"}]}}),
            line({"type": "system", "subtype": "task_started",
                  "task_id": "b7cgk2tn3", "tool_use_id": "call_bg1",
                  "task_type": "local_bash", "description": "sleep 6"}),
            line({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.01, "usage": {"input_tokens": 10},
                  "num_turns": 1, "duration_ms": 8830}),
            line({"type": "system", "subtype": "task_updated",
                  "task_id": "b7cgk2tn3", "patch": {"status": "running"}}),
            line({"type": "system", "subtype": "task_notification",
                  "task_id": "b7cgk2tn3", "tool_use_id": "call_bg1",
                  "status": "completed", "output_file": "", "summary": "sleep 6",
                  "usage": {"total_tokens": 0, "tool_uses": 0,
                            "duration_ms": 6000}}),
            line({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "DONE"}]}}),
            line({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.02, "usage": {"input_tokens": 15},
                  "num_turns": 2, "duration_ms": 14830}),
        ]

    async def test_mid_result_does_not_end_turn(self, tmp_path: Path):
        """P0 (slice-077-prefix 失败测试 1): 第一个 result 出现在 task_started
        之后、task_notification 之前——只是 cc 的前台回复边界。send 必须继续读
        到 task_notification 触发的 assistant 'DONE' + 最终 result，全程一个
        finished，且 finished 携带最终值（num_turns=2）。

        旧代码：第一个 result 直接 break → text 不含 'DONE'，finished.num_turns=1。
        新代码必须红→绿。
        """
        proc = FakeProc(self._bg_local_bash_lines())
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("run sleep 6 in background"))
        text = "".join(e.text for e in events if isinstance(e, TextEvent))
        assert "后台跑着" in text, "前台回复文本必须进入本轮"
        assert "DONE" in text, (
            "后台 task_notification 触发的 assistant 续跑文本必须进入本轮，"
            "而不是留给下一轮"
        )
        finished = [e for e in events if isinstance(e, FinishedEvent)]
        assert len(finished) == 1, "一个逻辑 turn 最多一个 finished (C-3)"
        assert finished[0].num_turns == 2, (
            "finished 必须是最终 result（num_turns=2），不是中间 result（num_turns=1）"
        )

    async def test_second_sendtext_rejected_while_turn_active(
        self, tmp_path: Path
    ):
        """P0 (slice-077-prefix 失败测试 2): 上一轮还在跑（stdout reader 活着）
        时，第二次 SendText 必须被拒绝——yield turn_in_progress error，且不向
        stdin 写第二条用户消息。否则两条 readline 抢同一根 pipe，第二条文本
        串入上一轮 (C-1 / C-4)。"""
        proc = FakeProc([line(init_event())], feed_eof=False)  # cc 还在跑
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        # 第一轮 send 读到 init 后挂在 readline，running=True
        task1 = asyncio.create_task(collect(host.send("first")))
        await asyncio.sleep(0.15)
        assert host.running is True, "第一轮 send 应在跑"
        written_before = list(proc.stdin.written)
        # 第二次 SendText 必须被拒绝（不写 stdin、yield turn_in_progress）
        try:
            events2 = await asyncio.wait_for(
                collect(host.send("second")), timeout=2.0
            )
        except asyncio.TimeoutError:
            events2 = []
        errors = [e for e in events2 if isinstance(e, ErrorEvent)]
        assert any(
            getattr(e, "subclass", "") == "turn_in_progress" for e in errors
        ), "上一轮在跑时第二次 SendText 必须 yield turn_in_progress error"
        assert proc.stdin.written == written_before, (
            "拒绝的 send 不能写 stdin（否则第二条用户消息串入上一轮）"
        )
        # 清理
        proc.stdout.feed_eof()
        task1.cancel()
        try:
            await task1
        except (asyncio.CancelledError, Exception):
            pass

    async def test_two_concurrent_tasks_end_only_at_final_result(
        self, tmp_path: Path
    ):
        """slice-077-prefix 失败测试 3 + 通过标准 5: 两个并发后台任务，中间
        result（两个都在跑）不结束 turn；A 先完成（B 还在 → 仍不结束）；B 完成
        后的最终 result 才结束。重复 progress / notification 幂等。全程一个
        finished。"""
        proc = FakeProc([
            line(init_event(sid="s-two")),
            line({"type": "system", "subtype": "task_started",
                  "task_id": "A", "tool_use_id": "call_a",
                  "task_type": "local_bash"}),
            line({"type": "system", "subtype": "task_started",
                  "task_id": "B", "tool_use_id": "call_b",
                  "task_type": "local_bash"}),
            # mid result: both A and B still pending → keep draining
            line({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.01, "usage": {"input_tokens": 10},
                  "num_turns": 1, "duration_ms": 1000}),
            # duplicate progress for A (idempotent, must not invent/double-count)
            line({"type": "system", "subtype": "task_progress",
                  "task_id": "A", "tool_use_id": "call_a",
                  "last_tool_name": "Bash"}),
            line({"type": "system", "subtype": "task_progress",
                  "task_id": "A", "tool_use_id": "call_a",
                  "last_tool_name": "Bash"}),
            # A terminates; B still pending → a following result stays mid-turn
            line({"type": "system", "subtype": "task_notification",
                  "task_id": "A", "tool_use_id": "call_a",
                  "status": "completed", "usage": {}}),
            line({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.015, "usage": {"input_tokens": 15},
                  "num_turns": 1, "duration_ms": 1500}),
            # duplicate terminal notification for A (idempotent)
            line({"type": "system", "subtype": "task_notification",
                  "task_id": "A", "tool_use_id": "call_a",
                  "status": "completed", "usage": {}}),
            # B terminates → no pending → next result is terminal
            line({"type": "system", "subtype": "task_notification",
                  "task_id": "B", "tool_use_id": "call_b",
                  "status": "completed", "usage": {}}),
            line({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "both done"}]}}),
            line({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.02, "usage": {"input_tokens": 20},
                  "num_turns": 2, "duration_ms": 2000}),
        ])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("run A and B"))
        finished = [e for e in events if isinstance(e, FinishedEvent)]
        assert len(finished) == 1, (
            "双任务只在最后一个终止 + 最终 result 后结束，一个 finished"
        )
        assert finished[0].num_turns == 2
        text = "".join(e.text for e in events if isinstance(e, TextEvent))
        assert "both done" in text

    async def test_disconnect_drain_reads_to_logical_terminal(
        self, tmp_path: Path
    ):
        """slice-077-prefix 失败测试 5 + §6: SSE cancel 发生在中间 result 之后，
        后台 drain 必须用与 live send 相同的逻辑 turn 终止条件——读到
        task_notification + 最终 result（tracker 清空）才结束。中途 result 不
        结束 drain；全程单 readline 消费者（send 入口 await drain 保证）。"""
        proc = FakeProc([
            line(init_event(sid="s-drain")),
            line({"type": "system", "subtype": "task_started",
                  "task_id": "bg1", "tool_use_id": "call_bg",
                  "task_type": "local_bash"}),
            # mid result: bg1 pending → live send keeps draining
            line({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.01, "usage": {}, "num_turns": 1,
                  "duration_ms": 1}),
        ], feed_eof=False)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))

        async def consume() -> None:
            async for _ in host.send("run bg"):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.15)  # send 读到 mid result，挂在 readline
        task.cancel()  # 模拟前端 SSE 断开
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)  # drain task 调度
        assert host._drain_task is not None, "cancel 后应起后台 drain"
        assert host.running is True, "drain 期间 running=True (slice §6)"
        # feed 后台通知 + 最终 result（无更多 pending）
        for ev in [
            {"type": "system", "subtype": "task_notification",
             "task_id": "bg1", "tool_use_id": "call_bg",
             "status": "completed"},
            {"type": "result", "subtype": "success", "is_error": False,
             "total_cost_usd": 0.02, "usage": {}, "num_turns": 2,
             "duration_ms": 2},
        ]:
            proc.stdout.feed_data((line(ev) + "\n").encode())
        proc.stdout.feed_eof()
        # drain 读到 final result（tracker 空）→ return
        if host._drain_task is not None:
            await asyncio.wait_for(host._drain_task, timeout=2.0)
        assert host._drain_task is None, "drain 应在逻辑终态结束"
        assert host.running is False
        assert proc.returncode is None, "drain 绝不杀 cc (slice §6)"

    async def test_drain_period_rejects_new_sendtext(self, tmp_path: Path):
        """codex C1 / C-4: 前端断开后 drain 在后台跑（running=True），新 SendText
        必须被并发门禁拒（turn_in_progress），不写 stdin——保证 drain 跑时新 send
        不会抢先 reset tracker 或抢同一根 stdout pipe。

        C1 的 reset-顺序 race 本身在 asyncio FIFO 下无法在单测触发（create_task
        后 drain 总先于新 send 调度，设 running=True），所以门禁是第一道防线、
        reset 移到 await drain 之后是 defense-in-depth；这个测试钉住第一道防线。
        """
        proc = FakeProc([
            line(init_event(sid="s-c1")),
            line({"type": "system", "subtype": "task_started",
                  "task_id": "bg1", "tool_use_id": "call_bg",
                  "task_type": "local_bash"}),
        ], feed_eof=False)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        # 第一轮：读 init + task_started，挂 readline → cancel 触发 drain
        task1 = asyncio.create_task(collect(host.send("first")))
        await asyncio.sleep(0.15)
        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)  # drain 起来，running=True
        assert host._drain_task is not None
        assert host.running is True, "drain 期间 running 必须为 True"
        written_before = list(proc.stdin.written)
        # 新 SendText 必须被拒（drain 还在跑）
        events2 = await collect(host.send("second"))
        errors = [e for e in events2 if isinstance(e, ErrorEvent)]
        assert any(
            getattr(e, "subclass", "") == "turn_in_progress" for e in errors
        ), "drain 期间新 SendText 必须被门禁拒（不抢 pipe / 不抢先 reset）"
        assert proc.stdin.written == written_before, "拒绝的 send 不能写 stdin"
        # 清理：让 drain 结束
        proc.stdout.feed_eof()
        if host._drain_task is not None:
            try:
                await asyncio.wait_for(host._drain_task, timeout=2.0)
            except (asyncio.CancelledError, Exception):
                pass

    async def test_error_result_kills_process_for_generation_isolation(
        self, tmp_path: Path
    ):
        """codex C3: error result 后必须杀进程（finally _sync_kill），下次
        send respawn 隔离旧 generation 的晚到事件。只 reset tracker 不够。"""
        proc = FakeProc([
            line(init_event(sid="s-c3")),
            line({"type": "result", "subtype": "max_turns", "is_error": True,
                  "errors": ["exceeded"], "total_cost_usd": 0.0,
                  "usage": {}, "num_turns": 10}),
        ])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) >= 1, "error result 必须 yield error terminal"
        assert proc.returncode is not None, (
            "error result 后必须杀进程（finally _sync_kill），实现 generation 隔离 (C3)"
        )

    async def test_stalled_does_not_fire_while_background_active(
        self, tmp_path: Path
    ):
        """codex H2 (C-7): 后台任务在跑时 stalled 不该触发——合法等待 Bash/Agent
        完成，不是 hang。pending 清空（最后一个 task_notification terminal）后
        stall 才恢复正常计时（C-8）。"""
        proc = FakeProc([
            line(init_event(sid="s-h2")),
            line({"type": "system", "subtype": "task_started",
                  "task_id": "bg1", "tool_use_id": "call_bg",
                  "task_type": "local_bash"}),
        ], feed_eof=False)
        host = CCHost(
            "sid", tmp_path, spawner=FakeSpawner([proc]),
            stalled_threshold_mild=0.1, stalled_threshold_severe=0.2,
            stalled_threshold_kill=0.3, stalled_tick=0.05,
        )
        task = asyncio.create_task(collect(host.send("hi")))
        await asyncio.sleep(0.5)  # 远超 kill 阈值 0.3s
        assert proc.returncode is None, (
            "后台 task 在跑时 stalled 不该 kill 进程 (C-7)"
        )
        proc.stdout.feed_eof()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_proc_exit_without_result_emits_host_error(
        self, tmp_path: Path
    ):
        """codex H6 (C-3): CC 进程退出/stdout EOF 且没发 result 时，必须发唯一
        terminal (host_error)，否则 turn 没有终态。"""
        proc = FakeProc([line(init_event(sid="s-h6"))], feed_eof=True)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert any(getattr(e, "subclass", "") == "host_error" for e in errors), (
            "proc EOF 无 result 必须发 host_error 保证一轮一终态 (C-3 / H6)"
        )


class TestExitSession:
    """slice-028 bug3: /exit (alias /quit) — CC's stream-json mode does NOT
    intercept the literal "/exit" string, so the host shuts CC down via the
    control_request(subtype=end_session) channel and yields SessionExitedEvent."""

    async def test_exit_writes_end_session_control_request(self, tmp_path: Path):
        """/exit → 写 control_request(subtype=end_session) + yield SessionExitedEvent。"""
        proc = FakeProc([])  # cc 立刻 EOF（优雅退出）
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
        """/quit 是 /exit 的别名，同样触发 ExitSession。"""
        proc = FakeProc([])
        proc.returncode = 0
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("/quit"))
        written = b"".join(proc.stdin.written).decode()
        assert '"subtype": "end_session"' in written
        assert any(isinstance(e, SessionExitedEvent) for e in events)

    async def test_exit_does_not_send_user_message(self, tmp_path: Path):
        """/exit 走的是 control_request 通道，绝不能把 '/exit' 当 user 消息发给 cc。"""
        proc = FakeProc([])
        proc.returncode = 0
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("/exit"))
        written = b"".join(proc.stdin.written).decode()
        assert '"type": "user"' not in written
        assert "/exit" not in written

    async def test_exit_fallback_sigkill_when_cc_does_not_die(self, tmp_path: Path):
        """cc 没在 timeout 内退出 → 兜底 SIGKILL + 仍 yield SessionExitedEvent。"""
        proc = FakeProc([])
        # returncode 保持 None（cc 不退）；FakeProc.kill() 会设 -9
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("/exit"))
        exited = [e for e in events if isinstance(e, SessionExitedEvent)]
        assert len(exited) == 1
        assert exited[0].returncode == -9  # SIGKILL 兜底


class TestLocalAndRestart:
    async def test_cost_does_not_hit_cc(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("hi"))  # accumulate cost
        proc.stdin.written.clear()
        events = await collect(host.send("/cost"))
        assert any(isinstance(e, LocalCommandEvent) for e in events)
        # nothing written to CC for a local command
        assert proc.stdin.written == []

    async def test_effort_triggers_restart_next_send(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        proc2 = FakeProc([line(init_event(sid="s-1")), line(result_ok())])
        spawner = FakeSpawner([proc1, proc2])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        await collect(host.send("/effort high"))
        assert host.effort == "high"
        # next real send respawns because interrupt/restart killed the process
        await collect(host.send("again"))
        # second proc was launched resuming the same cc session id
        assert len(spawner.spawned) >= 2
        second_args = spawner.spawned[1][0]
        assert "--resume" in second_args
        assert "s-1" in second_args

    async def test_model_emits_model_changed_for_immediate_sync(
        self, tmp_path: Path
    ):
        """slice-027 C2: /model emits ModelChangedEvent so the StatusBar syncs
        immediately, not on the next message (CC is lazy-restarted by the next
        send's _ensure_process)."""
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

    async def test_effort_emits_model_changed_with_effort(
        self, tmp_path: Path
    ):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/effort high"))
        mc = next(e for e in events if e.type == "model_changed")
        assert mc.effort == "high"

    async def test_bare_model_does_not_kill_process(self, tmp_path: Path):
        """slice-027: bare /model (no arg) must NOT kill the live CC process —
        the frontend picker intercepts it; if it reaches the backend anyway,
        we emit a usage hint and leave the process alone (no kill+respawn)."""
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/model"))
        # only the first send spawned — /model no-arg did not kill+respawn
        assert len(spawner.spawned) == 1
        # emits a usage local_command, NOT a model_changed
        assert "model_changed" not in [e.type for e in events]
        assert any(e.type == "local_command" for e in events)

    async def test_model_picked_args_reach_next_spawn(self, tmp_path: Path):
        """slice-027: after /model opus, the NEXT send's _ensure_process
        spawns CC with --model opus (lazy restart applies the new model)."""
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
        # fake proc has no real process group; emulate SIGINT by marking dead
        host._interrupt_proc = lambda p: setattr(p, "returncode", 0)
        await collect(host.send("hi"))
        await host.interrupt()
        # process is dead now
        assert host.is_dead is True
        await collect(host.send("next"))
        assert len(spawner.spawned) == 2
        second_args = spawner.spawned[1][0]
        assert "--resume" in second_args and "s-1" in second_args


class TestStalled:
    async def test_stalled_emits_phased_warnings_then_kill(self, tmp_path: Path):
        """stall 检测分阶段，不杀进程：mild (120s) + severe (300s) 各发一次
        StalledWarningEvent，cc 不 restart；30min hard cap (1800s) 才 kill +
        ErrorEvent 结束 turn。验证 dc804194 类的"等 GLM 响应慢"不再被误杀。"""
        proc1 = FakeProc([line(init_event())], feed_eof=False)
        spawner = FakeSpawner([proc1])
        clock = {"t": 0.0}

        def fake_now() -> float:
            clock["t"] += 150.0  # 跨过 mild(120) → severe(300) → kill(1800)
            return clock["t"]

        host = CCHost(
            "sid", tmp_path, spawner=spawner,
            now=fake_now,
            stalled_threshold_mild=120.0,
            stalled_threshold_severe=300.0,
            stalled_threshold_kill=1800.0,
            stalled_tick=0.001,
        )
        events = await collect(host.send("long job"))
        warnings = [e for e in events if isinstance(e, StalledWarningEvent)]
        severities = [w.severity for w in warnings]
        # mild + severe 各只发一次（per-turn 去重）
        assert severities.count("mild") == 1
        assert severities.count("severe") == 1
        # 30min hard cap 触发 ErrorEvent(stalled) 结束 turn
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert any(e.subclass == "stalled" for e in errors)
        # 不 restart —— cc 被 kill 但没 respawn（spawned 只有 proc1）
        assert len(spawner.spawned) == 1


class TestCancellation:
    async def test_cancelled_send_keeps_cc_alive(self, tmp_path: Path):
        """slice-028 v2 多 session: 前端断开（刷新/切换）= 暂时不消费 events，
        绝不杀 cc。cc 必须继续跑完 turn，刷新后 reconcile + resume 才能看到
        完整对话（单 session 时代这里 _sync_kill 会中断 AI 思考，丢回复）。
        后台 drain 接管读 stdout 到 result，cc 留活。"""
        # proc that blocks forever on stdout (no data, no EOF) — cc "还在思考"
        proc = FakeProc([], feed_eof=False)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]),
                      stalled_threshold_mild=10000.0, stalled_tick=0.01)

        async def _run():
            async for _ in host.send("hi"):
                pass

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)  # let it enter the readline wait
        task.cancel()  # 模拟前端 SSE 断开（刷新）
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)  # 让后台 drain task 调度
        # cc 没被杀——多 session 模型：刷新/切换保留 cc 活，reconcile 能找回
        assert proc.returncode is None

    async def test_drain_task_holds_strong_ref_and_clears(self, tmp_path: Path):
        """slice-028 v2 CR(F1): 后台 drain task 必须持强引用（防 Python event
        loop 弱引用 GC），close() 取消后清空。否则大 turn + 刷新时 drain 被 GC，
        cc stdout 缓冲满 → cc hang。"""
        # 只喂 init，之后阻塞（cc "还在思考"，drain 不会自行结束）
        proc = FakeProc([line(init_event())], feed_eof=False)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]),
                      stalled_threshold_mild=10000.0, stalled_tick=0.01)

        async def _run():
            async for _ in host.send("hi"):
                pass

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)
        task.cancel()  # 触发后台 drain
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.02)  # 让 drain task 启动
        # drain 仍在跑（proc 阻塞）→ 强引用在（不会被 GC）
        assert host._drain_task is not None
        # close 取消 drain + 清引用 + 杀 cc
        await host.close()
        assert host._drain_task is None


class TestElicitAnswer:
    """answer_elicit / cancel_elicit write control_response to stdin (slice-025-c).

    Ground truth: reverse_cc samples/raw/052_askuser_bypass_stdio.jsonl —
    AskUserQuestion triggers control_request(can_use_tool); the host must reply
    with control_response(behavior=allow, updatedInput={questions, answers,
    annotations}). answers is a required record (052 ZodError when absent).
    """

    async def test_answer_elicit_writes_allow_with_answers(self, tmp_path: Path):
        proc = FakeProc([])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        host._proc = proc
        host._pending_elicit = {
            "request_id": "req-1",
            "tool_use_id": "call_abc",
            "questions": [{"question": "A or B?", "header": "Pref",
                           "options": [{"label": "A"}], "multiSelect": False}],
        }
        ok = await host.answer_elicit({"A or B?": "A"})
        assert ok is True
        assert host._pending_elicit is None
        payload = json.loads(proc.stdin.written[-1].decode())
        assert payload["type"] == "control_response"
        assert payload["response"]["subtype"] == "success"
        assert payload["response"]["request_id"] == "req-1"
        assert payload["response"]["response"]["behavior"] == "allow"
        updated = payload["response"]["response"]["updatedInput"]
        assert updated["answers"] == {"A or B?": "A"}
        assert updated["questions"][0]["header"] == "Pref"
        assert updated["annotations"] == {}

    async def test_cancel_elicit_writes_deny(self, tmp_path: Path):
        proc = FakeProc([])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        host._proc = proc
        host._pending_elicit = {"request_id": "req-2", "tool_use_id": "c",
                                "questions": []}
        ok = await host.cancel_elicit()
        assert ok is True
        assert host._pending_elicit is None
        payload = json.loads(proc.stdin.written[-1].decode())
        assert payload["response"]["response"]["behavior"] == "deny"
        assert "declined" in payload["response"]["response"]["message"]

    async def test_answer_without_pending_returns_false(self, tmp_path: Path):
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([FakeProc([])]))
        ok = await host.answer_elicit({"q": "a"})
        assert ok is False


# ---------------------------------------------------------------------------
# slice-026 E1: checkpoint integration on send()
# ---------------------------------------------------------------------------


def _git_repo(path: Path) -> Path:
    """Init a tmp git repo with one commit; return its root."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return path


class TestCheckpointOnSend:
    async def test_non_git_workdir_yields_non_revertible_turn_start(self, tmp_path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        assert isinstance(events[0], TurnStartEvent)
        assert events[0].revertible is False
        assert events[0].turn_id

    async def test_fresh_turn_1_revertible_via_session_start_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # slice-026: fresh session turn 1 IS revertible — the session-start
        # checkpoint is saved in the init handler once cc_session_id is known
        # (worktree is still pristine at init).
        repo = _git_repo(tmp_path / "repo")
        proj = tmp_path / "projects"
        slug_dir = proj / workdir_to_slug(str(repo))
        slug_dir.mkdir(parents=True)
        (slug_dir / "new-sid.jsonl").write_text("")  # empty jsonl → offset 0
        monkeypatch.setattr("trowel_py.cc_host.service.cc_projects_root", lambda: proj)

        proc = FakeProc([line(init_event(sid="new-sid")), line(result_ok())])
        host = CCHost("sid", str(repo), spawner=FakeSpawner([proc]))
        events = await collect(host.send("first turn"))
        ts = next(e for e in events if isinstance(e, TurnStartEvent))
        assert ts.revertible is True
        assert host._session_start_saved is True
        saved = [
            m for m in checkpoint.list_checkpoints(str(repo)) if m.turn_id == ts.turn_id
        ]
        assert len(saved) == 1

    async def test_resumed_git_session_turn_is_revertible(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        repo = _git_repo(tmp_path / "repo")
        # fake the resumed jsonl under a tmp cc_projects_root
        proj = tmp_path / "projects"
        slug_dir = proj / workdir_to_slug(str(repo))
        slug_dir.mkdir(parents=True)
        (slug_dir / "cc-sid-1.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "prior"}}) + "\n"
        )
        monkeypatch.setattr("trowel_py.cc_host.service.cc_projects_root", lambda: proj)

        proc = FakeProc([line(init_event(sid="cc-sid-1")), line(result_ok())])
        host = CCHost("sid", str(repo), resume_from="cc-sid-1", spawner=FakeSpawner([proc]))
        events = await collect(host.send("next turn"))
        ts = next(e for e in events if isinstance(e, TurnStartEvent))
        assert ts.revertible is True
        # the checkpoint was actually written to the repo's git
        saved = [m for m in checkpoint.list_checkpoints(str(repo)) if m.turn_id == ts.turn_id]
        assert len(saved) == 1
        assert saved[0].jsonl_offset is not None

    async def test_reload_kills_process_for_resume(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("hi"))
        assert host.is_dead is False  # process still alive after a clean turn
        await host.reload()
        assert host.is_dead is True


class TestWorkflowTurnBoundary:
    """slice-036 fix: turn 边界回归 cc 的 `result` 事件,移除 all_done+silent→break。

    实测时序(cc 2.1.197,TaskOutput 阻塞模式):workflow 完成(all_done True)
    → cc think 静默 ~4s → 反馈 text → result。旧的 all_done+1s silent→break
    在 think 静默期误判,提前结束 turn,丢反馈 text + result。建立 cc 的
    turn 边界(result)后,send 应等到 result 才结束,不因 all_done+静默提前 break。
    """

    async def test_all_done_silent_does_not_break_waits_for_result(
        self, tmp_path: Path
    ) -> None:
        """all_done=True + cc 静默期,send 不提前 break,继续读到 result。"""
        # feed 不含 result;result 延迟 feed 模拟 cc 完成后的 think 静默期。
        proc = FakeProc([line(init_event())], feed_eof=False)
        host = CCHost(
            "sid", tmp_path, spawner=FakeSpawner([proc]),
            stalled_tick=0.001,
            stalled_threshold_mild=120.0,
            stalled_threshold_severe=300.0,
            stalled_threshold_kill=1800.0,
        )
        # 模拟 workflow 已完成:watcher enabled + all_done=True。
        w = host._workflow_watcher
        w._enabled = True
        w._tracked.add("wf_x")
        w._finished.add("wf_x")
        assert w.enabled and w.all_done

        async def feed_result_after_silence() -> None:
            # 让 send 先经历几次 readline timeout(all_done=True,cc 静默)。
            await asyncio.sleep(0.02)
            proc.stdout.feed_data(line(result_ok()).encode() + b"\n")

        task = asyncio.create_task(feed_result_after_silence())
        events = await collect(host.send("跑个 workflow"))
        await task
        # 旧逻辑:all_done+silent→break,send 在静默期结束,读不到 result。
        # 新逻辑:不 break,等到 result。断言读到 FinishedEvent(result)。
        assert any(isinstance(e, FinishedEvent) for e in events), (
            "all_done+silent 不应提前 break;send 应读到 cc 推的 result"
        )


class TestMemoryInjection:
    """slice-039: cc spawns carry memory injection via --append-system-prompt.

    Injection rides cc's native flag at spawn (spike 2026-07-09), NOT a
    reverse-proxy system rewrite. A read failure must degrade to "" and never
    block the spawn.
    """

    async def test_spawn_passes_memory_injection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trowel_py.cc_host.service as svc

        monkeypatch.setattr(
            svc, "build_memory_injection", lambda now, **kw: "INJECTION_MARKER_XYZ"
        )
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        i = args.index("--append-system-prompt")
        assert args[i + 1] == "INJECTION_MARKER_XYZ"

    async def test_spawn_survives_injection_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(now: str, **kw) -> str:
            raise RuntimeError("memory dir gone")

        import trowel_py.cc_host.service as svc

        monkeypatch.setattr(svc, "build_memory_injection", boom)
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner)
        events = await collect(host.send("hi"))  # does not raise
        args = spawner.spawned[0][0]
        assert "--append-system-prompt" not in args  # degraded, no flag
        assert any(isinstance(e, FinishedEvent) for e in events)


class TestMcpConfig:
    """slice-040-c: CCHost passes --mcp-config + injects memory identity env.

    The stdio MCP subprocess inherits cc's process.env, so identity
    (TROWEL_SESSION_ID / MEMORY_ROOT / CC_SESSION_ID) is injected here — the
    protocol layer carries no session id (reverse-engineered: only
    ``_meta.claudecode/toolUseId`` is sent per call).
    """

    async def test_spawn_args_include_mcp_config(self, tmp_path: Path) -> None:
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner, mcp_config="/tmp/mem.json")
        await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        assert "--mcp-config" in args
        assert "--strict-mcp-config" in args
        assert args[args.index("--mcp-config") + 1] == "/tmp/mem.json"

    async def test_spawn_no_mcp_config_flag_when_absent(self, tmp_path: Path) -> None:
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        assert "--mcp-config" not in args

    def test_build_spawn_env_injects_identity_when_mcp_config(
        self, tmp_path: Path
    ) -> None:
        host = CCHost("sid", tmp_path, mcp_config="/tmp/mem.json", proxy_base_url=None)
        env = host._build_spawn_env()
        assert env is not None
        assert env["TROWEL_SESSION_ID"] == "sid"
        assert "MEMORY_ROOT" in env
        # slice-078: host-neutral identity is always stamped on the CC path.
        assert env["TROWEL_HOST_KIND"] == "cc"

    def test_build_spawn_env_native_session_id_when_resumed(
        self, tmp_path: Path
    ) -> None:
        """slice-078: a resumed CC session knows cc_session_id at construction,
        so TROWEL_NATIVE_SESSION_ID (and the legacy CC_SESSION_ID) are stamped
        too — the memory MCP then writes host-neutral identity on first call."""
        host = CCHost(
            "sid",
            tmp_path,
            mcp_config="/tmp/mem.json",
            proxy_base_url=None,
            resume_from="cc-sid-xyz",
        )
        env = host._build_spawn_env()
        assert env is not None
        assert env["TROWEL_HOST_KIND"] == "cc"
        assert env["TROWEL_NATIVE_SESSION_ID"] == "cc-sid-xyz"
        assert env["CC_SESSION_ID"] == "cc-sid-xyz"

    def test_build_spawn_env_no_identity_without_mcp_config(
        self, tmp_path: Path
    ) -> None:
        """Pre-040-c behavior preserved: proxy off + no MCP → None (inherit)."""
        host = CCHost("sid", tmp_path, proxy_base_url=None)
        assert host._build_spawn_env() is None
