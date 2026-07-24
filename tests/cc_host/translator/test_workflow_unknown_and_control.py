import pytest

from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import ElicitationRequestEvent
from tests.cc_host.translator._support import cc


class TestIgnoreList:
    @pytest.mark.parametrize(
        "ev",
        [
            {"type": "system", "subtype": "post_turn_summary"},
            {"type": "system", "subtype": "task_updated"},
            {"type": "system", "subtype": "session_state_changed"},
            {"type": "system", "subtype": "files_persisted"},
            {"type": "system", "subtype": "elicitation_complete"},
            {"type": "system", "subtype": "prompt_suggestion"},
            {"type": "system", "subtype": "mcp_message"},
            {"type": "system", "subtype": "streamlined_stuff"},
            {"type": "unknown_thing"},
        ],
    )
    def test_ignored_event_yields_nothing(self, ev):
        assert Translator().translate(ev) == []


class TestElicitationRequest:
    def test_askuser_control_request_translates(self):
        ev = cc(
            type="control_request",
            request_id="req-1",
            request={
                "subtype": "can_use_tool",
                "tool_name": "AskUserQuestion",
                "display_name": "AskUserQuestion",
                "input": {
                    "questions": [
                        {
                            "question": "A or B?",
                            "header": "Pref",
                            "options": [
                                {"label": "A", "description": "a"},
                                {"label": "B"},
                            ],
                            "multiSelect": False,
                        }
                    ]
                },
                "tool_use_id": "call_abc",
            },
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, ElicitationRequestEvent)
        assert e.tool_use_id == "call_abc"
        assert e.request_id == "req-1"
        assert e.questions[0]["question"] == "A or B?"
        assert e.questions[0]["header"] == "Pref"
        assert e.questions[0]["multiSelect"] is False

    def test_non_askuser_control_request_yields_nothing(self):
        ev = cc(
            type="control_request",
            request_id="req-2",
            request={
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "ls"},
                "tool_use_id": "call_xyz",
            },
        )
        assert Translator().translate(ev) == []

    def test_non_can_use_tool_subtype_yields_nothing(self):
        ev = cc(
            type="control_request",
            request_id="req-3",
            request={
                "subtype": "set_max_thinking_tokens",
                "max_thinking_tokens": 1024,
            },
        )
        assert Translator().translate(ev) == []

    def test_askuserquestion_tool_use_skipped_no_tool_call(self):
        # AskUserQuestion 由 control_request 渲染，不走普通 tool_call。
        ev = cc(
            type="assistant",
            message={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_aq",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "question": "A?",
                                    "header": "P",
                                    "options": [{"label": "A"}],
                                    "multiSelect": False,
                                }
                            ]
                        },
                    },
                ]
            },
        )
        out = Translator().translate(ev)
        assert out == []
