from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import (
    ElicitationRequestEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserEvent,
)
from tests.cc_host.history._support import (
    _assistant,
    _init,
    _result_success,
    _tool_result,
    _user_text,
    _write_jsonl,
)


def test_parse_history_renders_user_then_assistant_text(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _init(),
            _user_text("hello"),
            _assistant([{"type": "text", "text": "hi there"}]),
            _result_success(),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")

    kinds = [type(e).__name__ for e in events]
    assert "SessionStartedEvent" in kinds
    assert "UserEvent" in kinds
    assert "TextEvent" in kinds
    assert "FinishedEvent" in kinds
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.text == "hello"
    text_ev = next(e for e in events if isinstance(e, TextEvent))
    assert text_ev.text == "hi there"

    assert kinds.index("UserEvent") < kinds.index("TextEvent")


def test_parse_history_maps_tool_use_and_result(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_text("write a file"),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "Write",
                        "input": {"file_path": "/tmp/x.txt", "content": "hi"},
                    }
                ]
            ),
            _tool_result("call_1", "wrote 1 file"),
            _assistant([{"type": "text", "text": "done"}]),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")
    call = next(e for e in events if isinstance(e, ToolCallEvent))
    assert call.tool_use_id == "call_1"
    assert call.tool_name == "Write"
    assert call.input == {"file_path": "/tmp/x.txt", "content": "hi"}
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.tool_use_id == "call_1"
    assert "wrote 1 file" in result.content


def test_parse_history_maps_askuserquestion_to_elicit_request(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_text("ask me A or B"),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "call_aq",
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "question": "A or B?",
                                    "header": "Pref",
                                    "options": [{"label": "A"}, {"label": "B"}],
                                    "multiSelect": False,
                                }
                            ]
                        },
                    }
                ]
            ),
            _tool_result("call_aq", 'User has answered: "A or B?"="A"'),
            _assistant([{"type": "text", "text": "got it"}]),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")
    elicit = next(e for e in events if isinstance(e, ElicitationRequestEvent))
    assert elicit.tool_use_id == "call_aq"
    assert elicit.questions[0]["header"] == "Pref"

    # control_request 不写入 JSONL；reducer 只需用 tool_use_id 匹配响应。
    assert elicit.request_id == ""
    assert not any(isinstance(e, ToolCallEvent) for e in events)
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.tool_use_id == "call_aq"


def test_parse_history_maps_thinking_block(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_text("think"),
            _assistant([{"type": "thinking", "thinking": "reasoning here"}]),
            _assistant([{"type": "text", "text": "answer"}]),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.text == "reasoning here"


def test_parse_history_missing_file_returns_empty(fake_projects: Path) -> None:
    events = history.parse_history("/workdir", "no-such-session")
    assert events == []


@pytest.mark.parametrize(
    "bad_id",
    ["", ".", "..", "../etc/passwd", "..\\windows", "a/b"],
)
def test_parse_history_rejects_traversal_ids(fake_projects: Path, bad_id: str) -> None:
    assert history.parse_history("/workdir", bad_id) == []


def test_parse_history_skips_unparseable_lines(fake_projects: Path) -> None:
    target = fake_projects / "abc-123.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write(json.dumps(_user_text("ok")) + "\n")
    events = history.parse_history("/workdir", "abc-123")
    assert any(isinstance(e, UserEvent) for e in events)


def test_parse_history_ignores_tool_result_only_user_as_user_event(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_text("do it"),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "Bash",
                        "input": {"command": "echo hi"},
                    }
                ]
            ),
            _tool_result("call_1", "hi\n"),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")

    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "do it"


def test_parse_history_user_message_as_list_text_blocks(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "你好，请问今天几号"}],
                },
            },
        ],
    )
    events = history.parse_history("/workdir", "abc-123")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "你好，请问今天几号"
