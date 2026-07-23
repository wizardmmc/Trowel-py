import json
import pytest

from trowel_py.cards.jsonl_parser import JsonlParseError, parse_jsonl


def test_parse_jsonl_normal():
    text = "\n".join(
        [
            json.dumps({"role": "user", "content": "what is X?"}),
            json.dumps({"role": "assistant", "content": "X is ..."}),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "what is X?"
    assert messages[1].role == "assistant"


def test_parse_jsonl_preserves_order():
    text = "\n".join(
        [
            json.dumps({"role": "user", "content": "q1"}),
            json.dumps({"role": "assistant", "content": "a1"}),
            json.dumps({"role": "user", "content": "q2"}),
        ]
    )
    messages = parse_jsonl(text)
    assert [m.content for m in messages] == ["q1", "a1", "q2"]


def test_parse_jsonl_empty_string_raises():
    with pytest.raises(JsonlParseError):
        parse_jsonl("")


def test_parse_jsonl_whitespace_only_raises():
    with pytest.raises(JsonlParseError):
        parse_jsonl("   \n  \n")


def test_parse_jsonl_skips_invalid_json_line():
    text = "\n".join(
        [
            json.dumps({"role": "user", "content": "q"}),
            "this is not json",
            json.dumps({"role": "assistant", "content": "a"}),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 2


def test_parse_jsonl_skips_missing_fields():
    text = "\n".join(
        [
            json.dumps({"role": "user"}),
            json.dumps({"content": "no role"}),
            json.dumps({"role": "user", "content": "ok"}),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].content == "ok"


def test_parse_jsonl_skips_non_user_assistant_role():
    text = "\n".join(
        [
            json.dumps({"role": "system", "content": "system prompt"}),
            json.dumps({"role": "tool", "content": "tool result"}),
            json.dumps({"role": "user", "content": "q"}),
            json.dumps({"role": "assistant", "content": "a"}),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 2
    assert {m.role for m in messages} == {"user", "assistant"}


def test_parse_jsonl_skips_non_string_content():
    text = "\n".join(
        [
            json.dumps({"role": "user", "content": ["block1", "block2"]}),
            json.dumps({"role": "assistant", "content": "valid"}),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].content == "valid"


def test_parse_jsonl_all_bad_lines_returns_empty():
    text = "\n".join(
        [
            json.dumps({"role": "system", "content": "x"}),
            "not json",
        ]
    )
    messages = parse_jsonl(text)
    assert messages == []


def test_parse_jsonl_ignores_blank_lines():
    text = "\n".join(
        [
            json.dumps({"role": "user", "content": "q"}),
            "",
            "   ",
            json.dumps({"role": "assistant", "content": "a"}),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 2


def test_parse_real_cc_user_nested_message_str_content():
    text = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": "what is useEffect?"},
            "sessionId": "abc",
        }
    )
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content == "what is useEffect?"


def test_parse_real_cc_assistant_content_list_keeps_text_only():
    text = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "internal reasoning here"},
                    {"type": "text", "text": "useEffect runs after render"},
                    {"type": "tool_use", "name": "Read", "input": {}},
                ],
            },
        }
    )
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert messages[0].content == "useEffect runs after render"


def test_parse_skips_thinking_only_content():
    text = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "hmm"}],
            },
        }
    )
    messages = parse_jsonl(text)
    assert messages == []


def test_parse_skips_non_message_meta_lines():
    text = "\n".join(
        [
            json.dumps({"type": "summary", "summary": "talked about react"}),
            json.dumps({"type": "mode", "mode": "default"}),
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "real question"},
                }
            ),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].content == "real question"


def test_parse_mixed_real_and_simplified_coexist():
    text = "\n".join(
        [
            json.dumps({"role": "user", "content": "simplified line"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "real cc line"}],
                    },
                }
            ),
        ]
    )
    messages = parse_jsonl(text)
    assert len(messages) == 2
    assert messages[0].content == "simplified line"
    assert messages[1].content == "real cc line"
