from __future__ import annotations

from pathlib import Path

from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import ErrorEvent, FinishedEvent, StatusEvent, TextEvent

from tests.cc_host.service._support import FakeProc, FakeSpawner, collect, line


class TestBackgroundTaskLogicalTurn:
    @staticmethod
    def _bg_local_bash_lines() -> list[str]:
        # 时序来自 CC 2.1.197 的 local_bash 隔离录制，字段已脱敏。
        return [
            line(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "s-bg",
                    "model": "glm-5.2",
                    "cwd": "/wd",
                    "tools": ["Read", "Bash"],
                }
            ),
            line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "后台跑着，等通知"}]
                    },
                }
            ),
            line(
                {
                    "type": "system",
                    "subtype": "task_started",
                    "task_id": "b7cgk2tn3",
                    "tool_use_id": "call_bg1",
                    "task_type": "local_bash",
                    "description": "sleep 6",
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
                    "duration_ms": 8830,
                }
            ),
            line(
                {
                    "type": "system",
                    "subtype": "task_updated",
                    "task_id": "b7cgk2tn3",
                    "patch": {"status": "running"},
                }
            ),
            line(
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "task_id": "b7cgk2tn3",
                    "tool_use_id": "call_bg1",
                    "status": "completed",
                    "output_file": "",
                    "summary": "sleep 6",
                    "usage": {"total_tokens": 0, "tool_uses": 0, "duration_ms": 6000},
                }
            ),
            line(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "DONE"}]},
                }
            ),
            line(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "total_cost_usd": 0.02,
                    "usage": {"input_tokens": 15},
                    "num_turns": 2,
                    "duration_ms": 14830,
                }
            ),
        ]

    @staticmethod
    def _bg_taskoutput_completed_lines() -> list[str]:
        # 真实录制中 TaskOutput 可通过 task_updated(completed) 结束，不一定再发 notification。
        fixture = (
            Path(__file__).parents[1] / "fixtures" / "bg_taskoutput_completed.jsonl"
        )
        return fixture.read_text(encoding="utf-8").splitlines()

    async def test_taskoutput_completed_without_notification_ends_turn(
        self, tmp_path: Path
    ):
        proc = FakeProc(self._bg_taskoutput_completed_lines())
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))

        events = await collect(host.send("run and wait with TaskOutput"))

        assert "EXACT_DONE" in "".join(
            event.text for event in events if isinstance(event, TextEvent)
        )
        finished = [event for event in events if isinstance(event, FinishedEvent)]
        assert len(finished) == 1, (
            "task_updated completed must clear pending before the final result"
        )
        assert not [event for event in events if isinstance(event, ErrorEvent)]
        assert not [
            event
            for event in events
            if isinstance(event, StatusEvent) and event.stage == "background_waiting"
        ], "the final result must not be reclassified as another waiting boundary"

    async def test_mid_result_does_not_end_turn(self, tmp_path: Path):
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
        assert len(finished) == 1, "一个逻辑 turn 最多一个 finished"
        assert finished[0].num_turns == 2, (
            "finished 必须是最终 result（num_turns=2），不是中间 result（num_turns=1）"
        )

    async def test_mid_result_surfaces_background_waiting_phase(self, tmp_path: Path):
        proc = FakeProc(self._bg_local_bash_lines())
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))

        events = await collect(host.send("run sleep 6 in background"))

        waiting = [
            event
            for event in events
            if isinstance(event, StatusEvent) and event.stage == "background_waiting"
        ]
        assert len(waiting) == 1
        waiting_index = events.index(waiting[0])
        done_text_index = next(
            i
            for i, event in enumerate(events)
            if isinstance(event, TextEvent) and event.text == "DONE"
        )
        assert waiting_index < done_text_index, (
            "等待态必须在后台完成触发的 CC 自动续跑之前到达前端"
        )
