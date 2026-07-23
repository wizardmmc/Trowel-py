from __future__ import annotations

import asyncio
from pathlib import Path

from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import (
    ErrorEvent,
    FinishedEvent,
    SessionExitedEvent,
    SessionStartedEvent,
    TextEvent,
    ToolCallEvent,
)

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
    text_delta,
)


class TestStartAndSend:
    async def test_send_cancel_does_not_kill_cc(self, tmp_path: Path):
        proc = FakeProc([line(init_event())], feed_eof=False)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        gen = host.send("hi")

        async def consume() -> None:
            async for _ in gen:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        assert proc.returncode is None, "前端断开不该杀 cc（多 session 模型）"

    async def test_send_yields_session_started_text_finished(self, tmp_path: Path):
        proc = FakeProc(
            [
                line(init_event()),
                line(text_delta(0, "hel")),
                line(text_delta(0, "lo")),
                line(result_ok()),
            ]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        kinds = [type(e).__name__ for e in events]
        assert kinds[0] == "TurnStartEvent"
        assert SessionStartedEvent in [type(e) for e in events]
        text_ev = [e for e in events if isinstance(e, TextEvent)]
        assert "".join(e.text for e in text_ev) == "hello"
        assert isinstance(events[-1], FinishedEvent)
        assert host.cc_session_id == "s-1"

    async def test_send_survives_overlong_readline_chunk(self, tmp_path: Path):
        # StreamReader 默认上限为 64 KiB；超长行应转成明确错误而不是冒泡成 host_error。
        overlong = '{"type":"stream_event","big":"' + ("x" * 80000) + '"}'
        proc = FakeProc(
            [
                line(init_event()),
                overlong,
                line(result_ok()),
            ]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert any(
            getattr(e, "subclass", None) == "overlong_line" for e in error_events
        )

    async def test_send_yields_session_exited_when_cc_exits(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        proc.returncode = 0
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        exited = [e for e in events if isinstance(e, SessionExitedEvent)]
        assert len(exited) == 1
        assert exited[0].returncode == 0

    async def test_send_no_session_exited_for_normal_turn(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
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
        proc = FakeProc(
            [
                line(init_event()),
                line(
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {
                                "type": "tool_use",
                                "id": "tu_1",
                                "name": "Write",
                            },
                        },
                    }
                ),
                line(
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": '{"p":"/a"}',
                            },
                        },
                    }
                ),
                line(
                    {
                        "type": "stream_event",
                        "event": {"type": "content_block_stop", "index": 0},
                    }
                ),
                line(result_ok()),
            ]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("write a file"))
        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(calls) == 1
        assert calls[0].tool_name == "Write"
