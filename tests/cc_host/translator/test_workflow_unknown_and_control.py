import pytest

from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import ElicitationRequestEvent, ToolCallEvent
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
        assert e.tool_name == "AskUserQuestion"
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

    @pytest.mark.parametrize(
        ("tool_name", "question"),
        [
            ("EnterPlanMode", "是否允许 Claude 进入计划模式？"),
            ("ExitPlanMode", "是否批准当前计划并退出计划模式？"),
        ],
    )
    def test_plan_mode_control_request_translates_to_confirmation(
        self,
        tool_name,
        question,
    ):
        ev = cc(
            type="control_request",
            request_id="req-plan",
            request={
                "subtype": "can_use_tool",
                "tool_name": tool_name,
                "input": {},
                "tool_use_id": "call-plan",
            },
        )

        out = Translator().translate(ev)

        assert len(out) == 1
        assert isinstance(out[0], ElicitationRequestEvent)
        assert out[0].tool_name == tool_name
        assert out[0].questions[0]["question"] == question
        assert out[0].questions[0]["multiSelect"] is False

    @pytest.mark.parametrize("tool_name", ["EnterPlanMode", "ExitPlanMode"])
    def test_plan_mode_tool_use_remains_visible(self, tool_name):
        ev = cc(
            type="assistant",
            message={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-plan",
                        "name": tool_name,
                        "input": {},
                    }
                ]
            },
        )

        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ToolCallEvent)
        assert out[0].tool_name == tool_name

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
