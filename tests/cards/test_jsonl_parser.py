"""Tests for the JSONL conversation parser (slice 018a).

The parser is a pure function (no DB, no LLM), so it gets the most coverage
per the test pyramid.
"""
import json
import pytest

from trowel_py.cards.jsonl_parser import ChatMessage, JsonlParseError, parse_jsonl


# --- happy path ---

def test_parse_jsonl_normal():
    """Valid user/assistant turns parse into ChatMessage objects."""
    text = "\n".join([
        json.dumps({"role": "user", "content": "what is X?"}),
        json.dumps({"role": "assistant", "content": "X is ..."}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "what is X?"
    assert messages[1].role == "assistant"


def test_parse_jsonl_preserves_order():
    """Messages keep their original line order."""
    text = "\n".join([
        json.dumps({"role": "user", "content": "q1"}),
        json.dumps({"role": "assistant", "content": "a1"}),
        json.dumps({"role": "user", "content": "q2"}),
    ])
    messages = parse_jsonl(text)
    assert [m.content for m in messages] == ["q1", "a1", "q2"]


# --- empty input -> raise (not return []) ---

def test_parse_jsonl_empty_string_raises():
    """Empty string is unusable input -> JsonlParseError."""
    with pytest.raises(JsonlParseError):
        parse_jsonl("")


def test_parse_jsonl_whitespace_only_raises():
    """Whitespace-only input is also treated as empty -> JsonlParseError."""
    with pytest.raises(JsonlParseError):
        parse_jsonl("   \n  \n")


# --- bad lines -> skip (not raise) ---

def test_parse_jsonl_skips_invalid_json_line():
    """A non-JSON line is skipped; valid lines still parse."""
    text = "\n".join([
        json.dumps({"role": "user", "content": "q"}),
        "this is not json",
        json.dumps({"role": "assistant", "content": "a"}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 2  # bad line skipped


def test_parse_jsonl_skips_missing_fields():
    """A line missing role or content is skipped."""
    text = "\n".join([
        json.dumps({"role": "user"}),           # no content
        json.dumps({"content": "no role"}),     # no role
        json.dumps({"role": "user", "content": "ok"}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].content == "ok"


def test_parse_jsonl_skips_non_user_assistant_role():
    """system / tool / unknown roles are skipped (ignored, not errors)."""
    text = "\n".join([
        json.dumps({"role": "system", "content": "system prompt"}),
        json.dumps({"role": "tool", "content": "tool result"}),
        json.dumps({"role": "user", "content": "q"}),
        json.dumps({"role": "assistant", "content": "a"}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 2
    assert {m.role for m in messages} == {"user", "assistant"}


def test_parse_jsonl_skips_non_string_content():
    """content as a list (real CC log shape) is skipped, not crashed on."""
    text = "\n".join([
        json.dumps({"role": "user", "content": ["block1", "block2"]}),
        json.dumps({"role": "assistant", "content": "valid"}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].content == "valid"


def test_parse_jsonl_all_bad_lines_returns_empty():
    """Non-empty file with only bad/system lines returns [] (does NOT raise)."""
    text = "\n".join([
        json.dumps({"role": "system", "content": "x"}),
        "not json",
    ])
    messages = parse_jsonl(text)
    assert messages == []


def test_parse_jsonl_ignores_blank_lines():
    """Blank lines between records are ignored, not treated as errors."""
    text = "\n".join([
        json.dumps({"role": "user", "content": "q"}),
        "",
        "   ",
        json.dumps({"role": "assistant", "content": "a"}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 2


# --- real CC format: nested message + content as list of blocks ---

def test_parse_real_cc_user_nested_message_str_content():
    """Real CC user line: role/content nested under message, content is str."""
    text = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": "what is useEffect?"},
        "sessionId": "abc",
    })
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content == "what is useEffect?"


def test_parse_real_cc_assistant_content_list_keeps_text_only():
    """Real CC assistant line: content is a list of blocks; only text kept."""
    text = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "internal reasoning here"},
                {"type": "text", "text": "useEffect runs after render"},
                {"type": "tool_use", "name": "Read", "input": {}},
            ],
        },
    })
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert messages[0].content == "useEffect runs after render"


def test_parse_skips_thinking_only_content():
    """A line whose content list has only thinking blocks -> skipped (no text)."""
    text = json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]},
    })
    messages = parse_jsonl(text)
    assert messages == []


def test_parse_skips_non_message_meta_lines():
    """summary / mode / attachment meta lines have no message -> skipped."""
    text = "\n".join([
        json.dumps({"type": "summary", "summary": "talked about react"}),
        json.dumps({"type": "mode", "mode": "default"}),
        json.dumps({"type": "user", "message": {"role": "user", "content": "real question"}}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 1
    assert messages[0].content == "real question"


def test_parse_mixed_real_and_simplified_coexist():
    """Both real CC (nested) and simplified (top-level) lines parse together."""
    text = "\n".join([
        json.dumps({"role": "user", "content": "simplified line"}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "real cc line"}]}}),
    ])
    messages = parse_jsonl(text)
    assert len(messages) == 2
    assert messages[0].content == "simplified line"
    assert messages[1].content == "real cc line"
