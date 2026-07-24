from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import (
    SessionStartedEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
)
from tests.cc_host.translator._support import cc


class TestSystemInit:
    def test_init_translates_to_session_started(self):
        ev = cc(
            type="system",
            subtype="init",
            model="glm-5.2",
            cwd="/tmp/x",
            session_id="s-1",
            tools=["Read", "Skill"],
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        s = out[0]
        assert isinstance(s, SessionStartedEvent)
        assert s.cc_session_id == "s-1"
        assert s.model == "glm-5.2"
        assert s.tools == ["Read", "Skill"]

    def test_init_passes_through_slash_rosters(self):
        ev = cc(
            type="system",
            subtype="init",
            model="glm-5.2",
            cwd="/tmp/x",
            session_id="s-1",
            tools=["Read"],
            slash_commands=["monthly-etf", "review"],
            skills=["monthly-etf"],
            agents=["claude", "general-purpose"],
        )
        out = Translator().translate(ev)
        s = out[0]
        assert s.slash_commands == ["monthly-etf", "review"]
        assert s.skills == ["monthly-etf"]
        assert s.agents == ["claude", "general-purpose"]

    def test_init_without_rosters_defaults_empty(self):
        ev = cc(
            type="system",
            subtype="init",
            model="glm-5.2",
            cwd="/tmp/x",
            session_id="s-1",
            tools=["Read"],
        )
        s = Translator().translate(ev)[0]
        assert s.slash_commands == []
        assert s.skills == []
        assert s.agents == []


class TestTextAndThinking:
    def test_text_delta_translates_to_text_event(self):
        ev = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hel"},
            },
        }
        out = Translator().translate(ev)
        assert [type(x) for x in out] == [TextEvent]
        assert out[0].text == "hel"

    def test_thinking_delta_translates_to_thinking_event(self):
        ev = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "text": "pondering"},
            },
        }
        out = Translator().translate(ev)
        assert isinstance(out[0], ThinkingEvent)
        assert out[0].text == "pondering"


class TestToolCallStitching:
    def _block_start(self):
        return {
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

    def _delta(self, chunk):
        return {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": chunk},
            },
        }

    def _stop(self):
        return {
            "type": "stream_event",
            "event": {"type": "content_block_stop", "index": 0},
        }

    def test_tool_use_stitched_to_tool_call_on_block_stop(self):
        t = Translator()
        assert t.translate(self._block_start()) == []
        assert t.translate(self._delta('{"path":"/a",')) == []
        assert t.translate(self._delta('"content":"x"}')) == []
        out = t.translate(self._stop())
        assert len(out) == 1
        call = out[0]
        assert isinstance(call, ToolCallEvent)
        assert call.tool_use_id == "tu_1"
        assert call.tool_name == "Write"
        assert call.input == {"path": "/a", "content": "x"}

    def test_assistant_envelope_backfills_tool_call_when_no_stream(self):
        t = Translator()
        envelope = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_z",
                        "name": "Read",
                        "input": {"path": "/b"},
                    },
                ]
            },
        }
        out = t.translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], ToolCallEvent)
        assert out[0].tool_use_id == "tu_z"
        assert out[0].input == {"path": "/b"}

    def test_assistant_envelope_does_not_duplicate_already_streamed_call(self):
        t = Translator()
        t.translate(self._block_start())
        t.translate(self._delta('{"path":"/a"}'))
        t.translate(self._stop())
        envelope = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "Write",
                        "input": {"path": "/a"},
                    },
                ]
            },
        }
        out = t.translate(envelope)
        assert out == []


# 缺少 stream delta 时，assistant envelope 是完整内容的回退来源。
class TestAssistantEnvelopeTextThinking:
    def test_assistant_envelope_text_is_emitted(self):
        t = Translator()
        envelope = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "今天是 2026年7月2日，星期四"}],
            },
        }
        out = t.translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], TextEvent)
        assert out[0].text == "今天是 2026年7月2日，星期四"

    def test_assistant_envelope_thinking_is_emitted(self):
        t = Translator()
        envelope = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "需要先查日期"}],
            },
        }
        out = t.translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], ThinkingEvent)
        assert out[0].text == "需要先查日期"

    def test_assistant_envelope_emits_thinking_text_tool_use_in_order(self):
        t = Translator()
        envelope = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "reason here"},
                    {"type": "text", "text": "the answer"},
                    {
                        "type": "tool_use",
                        "id": "tu_9",
                        "name": "Bash",
                        "input": {"command": "date"},
                    },
                ],
            },
        }
        out = t.translate(envelope)
        assert [type(x).__name__ for x in out] == [
            "ThinkingEvent",
            "TextEvent",
            "ToolCallEvent",
        ]
        assert out[2].tool_use_id == "tu_9"

    def test_subagent_tool_use_carries_parent_tool_use_id(self):
        envelope = {
            "type": "assistant",
            "parent_tool_use_id": "call_AGENT_X",
            "subagent_type": "general-purpose",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_BASH_Y",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ]
            },
        }
        out = Translator().translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], ToolCallEvent)
        assert out[0].parent_tool_use_id == "call_AGENT_X"

    def test_top_level_tool_use_has_no_parent(self):
        envelope = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_main",
                        "name": "Bash",
                        "input": {"command": "pwd"},
                    }
                ]
            },
        }
        out = Translator().translate(envelope)
        assert out[0].parent_tool_use_id is None
