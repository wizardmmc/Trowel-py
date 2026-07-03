"""Tests for the history replay translator (slice023-web).

parse_history() reads a CC session jsonl and returns trowel events that are
isomorphic to the live stream, so the frontend can render history + live with
one reducer. These tests inject a small fake jsonl via tmp_path + monkeypatch
of cc_projects_root, so no real CC state is touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import (
    FinishedEvent,
    SessionStartedEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserEvent,
)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    """Write a list of dicts as newline-delimited json."""
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _user_text(text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "timestamp": "2026-06-01T00:00:00Z",
    }


def _assistant(blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": blocks},
        "timestamp": "2026-06-01T00:00:05Z",
    }


def _tool_result(tool_use_id: str, content: str) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
            ],
        },
        "timestamp": "2026-06-01T00:00:10Z",
    }


def _init() -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "model": "glm-5.2",
        "cwd": "/tmp",
        "session_id": "abc-123",
        "tools": ["Read", "Write", "Bash"],
    }


def _result_success() -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "num_turns": 1,
    }


@pytest.fixture()
def fake_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point cc_projects_root at a tmp dir; return the slug dir to write into.

    The slug dir matches what parse_history('/workdir') will look up, so tests
    just write <cc_session_id>.jsonl into the returned path.
    """
    root = tmp_path / "projects"
    slug_dir = root / history.workdir_to_slug("/workdir")
    slug_dir.mkdir(parents=True)
    monkeypatch.setattr(history, "cc_projects_root", lambda: root)
    return slug_dir


def test_parse_history_renders_user_then_assistant_text(fake_projects: Path) -> None:
    """A plain user->assistant-text turn yields UserEvent then TextEvent."""
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
    assert "SessionStartedEvent" in kinds  # init mapped
    assert "UserEvent" in kinds
    assert "TextEvent" in kinds
    assert "FinishedEvent" in kinds
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.text == "hello"
    text_ev = next(e for e in events if isinstance(e, TextEvent))
    assert text_ev.text == "hi there"
    # user comes before assistant text
    assert kinds.index("UserEvent") < kinds.index("TextEvent")


def test_parse_history_maps_tool_use_and_result(fake_projects: Path) -> None:
    """tool_use block -> ToolCallEvent; tool_result user msg -> ToolResultEvent."""
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


def test_parse_history_maps_thinking_block(fake_projects: Path) -> None:
    """A thinking block surfaces as a ThinkingEvent (collapsed-by-default in UI)."""
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
    """A non-existent cc_session_id yields an empty list, not an error."""
    events = history.parse_history("/workdir", "no-such-session")
    assert events == []


@pytest.mark.parametrize(
    "bad_id",
    ["", ".", "..", "../etc/passwd", "..\\windows", "a/b"],
)
def test_parse_history_rejects_traversal_ids(
    fake_projects: Path, bad_id: str
) -> None:
    """Traversal-shaped cc_session_ids are refused before any file access."""
    assert history.parse_history("/workdir", bad_id) == []


def test_parse_history_skips_unparseable_lines(fake_projects: Path) -> None:
    """Garbage lines are skipped, not fatal."""
    target = fake_projects / "abc-123.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write(json.dumps(_user_text("ok")) + "\n")
    events = history.parse_history("/workdir", "abc-123")
    assert any(isinstance(e, UserEvent) for e in events)


def test_parse_history_ignores_tool_result_only_user_as_user_event(
    fake_projects: Path,
) -> None:
    """A user message whose content is a tool_result list must NOT become a
    UserEvent (it's a tool_result echo, not a user turn)."""
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
    # exactly one UserEvent (the real user text), not two
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "do it"


def test_parse_history_user_message_as_list_text_blocks(fake_projects: Path) -> None:
    """Real CC jsonl persists user turns as content=list[text] (NOT a plain
    string); such a message must still surface as a UserEvent. Ground truth:
    slice024 E2 — the E1 session's user bubble was missing from history because
    _translate_user only handled string content and silently dropped list-text.
    """
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
