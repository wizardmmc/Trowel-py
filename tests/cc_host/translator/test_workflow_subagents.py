from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import SubagentProgressEvent
from tests.cc_host.translator._support import cc


class TestSubagentProgress:
    def test_task_started_translates(self):
        ev = cc(
            type="system",
            subtype="task_started",
            task_id="t1",
            tool_use_id="call_x",
            description="Count files",
            subagent_type="general-purpose",
            task_type="local_agent",
            prompt="count",
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, SubagentProgressEvent)
        assert e.tool_use_id == "call_x"
        assert e.task_id == "t1"
        assert e.status == "started"
        assert e.description == "Count files"
        assert e.subagent_type == "general-purpose"
        assert e.last_tool_name is None
        assert e.usage is None

    def test_task_progress_translates(self):
        ev = cc(
            type="system",
            subtype="task_progress",
            task_id="t1",
            tool_use_id="call_x",
            description="Running Count",
            subagent_type="general-purpose",
            last_tool_name="Bash",
            usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 4865},
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, SubagentProgressEvent)
        assert e.status == "progress"
        assert e.last_tool_name == "Bash"
        assert e.usage == {"total_tokens": 10, "tool_uses": 1, "duration_ms": 4865}

    def test_task_notification_translates_to_completed(self):
        ev = cc(
            type="system",
            subtype="task_notification",
            task_id="t1",
            tool_use_id="call_x",
            status="completed",
            output_file="",
            summary="Count files",
            usage={"total_tokens": 0, "tool_uses": 2, "duration_ms": 13878},
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, SubagentProgressEvent)
        assert e.status == "completed"
        assert e.usage == {"total_tokens": 0, "tool_uses": 2, "duration_ms": 13878}

    def test_task_notification_passes_through_non_completed_status(self):
        ev_failed = cc(
            type="system",
            subtype="task_notification",
            task_id="t1",
            tool_use_id="call_x",
            status="failed",
            output_file="",
            summary="boom",
            usage={"total_tokens": 0, "tool_uses": 1, "duration_ms": 100},
        )
        out_failed = Translator().translate(ev_failed)
        assert isinstance(out_failed[0], SubagentProgressEvent)
        assert out_failed[0].status == "failed"
        ev_cancelled = cc(
            type="system",
            subtype="task_notification",
            task_id="t1",
            tool_use_id="call_x",
            status="cancelled",
            output_file="",
            summary="aborted",
            usage={"total_tokens": 0, "tool_uses": 0, "duration_ms": 0},
        )
        out_cancelled = Translator().translate(ev_cancelled)
        assert out_cancelled[0].status == "cancelled"

    def test_task_notification_without_status_surfaces_unknown(self):
        ev = cc(
            type="system",
            subtype="task_notification",
            task_id="t1",
            tool_use_id="call_x",
            output_file="",
            summary="no status",
        )
        out = Translator().translate(ev)
        assert isinstance(out[0], SubagentProgressEvent)
        assert out[0].status == "unknown"

    def test_task_updated_yields_nothing(self):
        # task_updated 与随后的 notification 重复，因此不向界面发送。
        ev = cc(
            type="system",
            subtype="task_updated",
            task_id="t1",
            patch={"status": "completed", "end_time": 1783036070663},
        )
        assert Translator().translate(ev) == []

    def test_sample030_subagent_tail(self):
        t = Translator()
        t.translate(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "thinking", "thinking": "need to count"}]
                },
            }
        )
        t.translate(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1ac8",
                            "name": "Agent",
                            "input": {
                                "description": "Count files",
                                "prompt": "...",
                                "subagent_type": "general-purpose",
                            },
                        }
                    ]
                },
            }
        )
        started = t.translate(
            cc(
                type="system",
                subtype="task_started",
                task_id="a53f",
                tool_use_id="call_1ac8",
                description="Count files in directory",
                subagent_type="general-purpose",
                task_type="local_agent",
                prompt="...",
            )
        )
        progress = t.translate(
            cc(
                type="system",
                subtype="task_progress",
                task_id="a53f",
                tool_use_id="call_1ac8",
                description="Running",
                subagent_type="general-purpose",
                last_tool_name="Bash",
                usage={"total_tokens": 0, "tool_uses": 1, "duration_ms": 4865},
            )
        )
        updated = t.translate(
            cc(
                type="system",
                subtype="task_updated",
                task_id="a53f",
                patch={"status": "completed", "end_time": 1783036070663},
            )
        )
        notification = t.translate(
            cc(
                type="system",
                subtype="task_notification",
                task_id="a53f",
                tool_use_id="call_1ac8",
                status="completed",
                output_file="",
                summary="Count files in directory",
                usage={"total_tokens": 0, "tool_uses": 2, "duration_ms": 13878},
            )
        )
        assert isinstance(started[0], SubagentProgressEvent)
        assert started[0].status == "started"
        assert progress[0].status == "progress"
        assert progress[0].last_tool_name == "Bash"
        assert updated == []
        assert notification[0].status == "completed"
