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
    ElicitationRequestEvent,
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


def _tool_result_with_tur(tool_use_id: str, content: str, tur: dict) -> dict:
    """A user-message row whose top level carries a cc ``toolUseResult``."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
            ],
        },
        "toolUseResult": tur,
    }


def test_parse_history_attaches_edit_write_diff_with_real_lines(
    fake_projects: Path,
) -> None:
    """slice-033 feat 2: cc's top-level toolUseResult.structuredPatch becomes a
    WriteDiff on ToolResultEvent, carrying REAL file line numbers (360, not 1).
    """
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_text("edit it"),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "call_e1",
                        "name": "Edit",
                        "input": {
                            "file_path": "/a/x.py",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    }
                ]
            ),
            _tool_result_with_tur(
                "call_e1",
                "The file x.py has been updated successfully.",
                {
                    "filePath": "/a/x.py",
                    "oldString": "a",
                    "newString": "b",
                    "originalFile": "a\n",
                    "structuredPatch": [
                        {
                            "oldStart": 360,
                            "oldLines": 1,
                            "newStart": 360,
                            "newLines": 1,
                            "lines": ["-a", "+b"],
                        }
                    ],
                },
            ),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.write_diff is not None
    assert result.write_diff.type == "update"
    assert result.write_diff.hunks[0].oldStart == 360
    assert result.write_diff.hunks[0].newStart == 360
    assert result.write_diff.hunks[0].lines == ("-a", "+b")


def test_parse_history_write_create_yields_create_write_diff(
    fake_projects: Path,
) -> None:
    """Write-create toolUseResult (type=create, empty patch) → create write_diff."""
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "call_w1",
                        "name": "Write",
                        "input": {"file_path": "/a/new.py", "content": "hi\n"},
                    }
                ]
            ),
            _tool_result_with_tur(
                "call_w1",
                "File created successfully at: /a/new.py",
                {
                    "type": "create",
                    "filePath": "/a/new.py",
                    "content": "hi\n",
                    "originalFile": "",
                    "structuredPatch": [],
                },
            ),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.write_diff is not None
    assert result.write_diff.type == "create"
    assert result.write_diff.hunks == ()


def test_parse_history_no_tool_use_result_means_no_write_diff(
    fake_projects: Path,
) -> None:
    """A tool_result whose row has no toolUseResult (old jsonl, Bash, …) →
    write_diff stays None; the FE falls back to its existing rendering."""
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "call_b1",
                        "name": "Bash",
                        "input": {"command": "echo hi"},
                    }
                ]
            ),
            _tool_result("call_b1", "hi"),  # helper builds a row WITHOUT toolUseResult
        ],
    )

    events = history.parse_history("/workdir", "abc-123")
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.write_diff is None


def test_parse_history_maps_askuserquestion_to_elicit_request(
    fake_projects: Path,
) -> None:
    """slice-025-c: an AskUserQuestion tool_use replays as elicit_request, and
    the matching tool_result flips it to answered via the reducer (the history
    translator only emits the events; the reducer does the flip)."""
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
    # request_id is not in the jsonl (control_request never persists); empty
    # is fine — the reducer matches on tool_use_id only.
    assert elicit.request_id == ""
    # No ToolCallEvent should be emitted for AskUserQuestion (it is not a
    # normal tool row in the replay view).
    assert not any(isinstance(e, ToolCallEvent) for e in events)
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.tool_use_id == "call_aq"


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


# --- slice-035 bug4: scrub CC-internal injections from reloaded user rows ------
#
# CC persists several "user" rows that are NOT real user input: skill
# description injections (isMeta=True), raw slash-command tags
# (<command-message>..</command-name>..</command-args>), the trowel-expanded
# skill trigger ("Use the Skill tool with skill='X'. ..."), and local
# command stdout (<local-command-stdout>). The live path never echoes them
# (translator._on_user only extracts tool_result); history must match, or
# reload shows a polluted user bubble. See slice-035 spec.


def _user_list_text(text: str, is_meta: bool = False) -> dict:
    """A user row whose content is one text block (the common CC jsonl
    shape). Optionally marked isMeta=True (skill descriptions, caveats)."""
    ev: dict = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "timestamp": "2026-06-01T00:00:00Z",
    }
    if is_meta:
        ev["isMeta"] = True
    return ev


def test_parse_history_skips_ismeta_user_row(fake_projects: Path) -> None:
    """A skill-description injection (isMeta=True) must NOT surface as a
    user bubble — the live path never shows it either."""
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_list_text(
                "Base directory for this skill: /Users/x/.claude/skills/grill-me\n\n"
                "Interview me relentlessly about every aspect of this plan.",
                is_meta=True,
            )
        ],
    )
    events = history.parse_history("/workdir", "abc-123")
    assert [e for e in events if isinstance(e, UserEvent)] == []


def test_parse_history_command_tags_restore_slash_name_args(
    fake_projects: Path,
) -> None:
    """Raw slash-command tags persisted by CC must restore to the original
    `/name args` (what the user typed), not leak the raw tags."""
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_list_text(
                "<command-message>grill-me</command-message>\n"
                "<command-name>/grill-me</command-name>\n"
                "<command-args>修复bug</command-args>"
            )
        ],
    )
    events = history.parse_history("/workdir", "abc-123")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "/grill-me 修复bug"


def test_parse_history_skill_trigger_prompt_restores_slash(
    fake_projects: Path,
) -> None:
    """The trowel-expanded skill trigger ('Use the Skill tool with ...') must
    also restore to `/name args` to match the live optimistic render."""
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [_user_list_text("Use the Skill tool with skill='grill-me'. 修复bug")],
    )
    events = history.parse_history("/workdir", "abc-123")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "/grill-me 修复bug"


def test_parse_history_local_command_stdout_not_rendered(
    fake_projects: Path,
) -> None:
    """Local-command stdout (/model, /effort outputs) must not render — live
    path handles these via RestartSession/LocalCommand, never in dialogue."""
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_list_text(
                "<local-command-stdout>Set model to glm-5.1 and saved as "
                "your default for new sessions</local-command-stdout>"
            )
        ],
    )
    events = history.parse_history("/workdir", "abc-123")
    assert [e for e in events if isinstance(e, UserEvent)] == []


def test_parse_history_real_user_text_unchanged(fake_projects: Path) -> None:
    """Genuine user input must pass through scrubbing untouched."""
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [_user_list_text("修复重载渲染的几个 bug")],
    )
    events = history.parse_history("/workdir", "abc-123")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "修复重载渲染的几个 bug"


# --- slice-031: replay thinking duration (timestamp-delta) ---------------------
#
# Live stream measures "Thought for Ns" via thinking_tokens heartbeat start time
# -> thinking envelope. JSONL has no heartbeat, but every entry carries an ISO
# timestamp; CC persists a thinking block as its own assistant entry, so the
# delta vs the previous entry's timestamp reconstructs the think duration. This
# is an approximation (matches CC TUI's behaviour on reload) — see slice-031.


def _ts_entry(type_: str, timestamp: str, content) -> dict:
    """Build a jsonl entry with an explicit timestamp + message.content."""
    return {
        "type": type_,
        "timestamp": timestamp,
        "message": {"role": type_, "content": content},
    }


def test_compute_thinking_duration_normal() -> None:
    """23-second gap between prev_ts and thinking ts yields 23."""
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:00.000Z", "2026-07-06T12:38:23.000Z"
        )
        == 23
    )


def test_compute_thinking_duration_no_prev() -> None:
    """First entry (no prev) -> None (frontend falls back to bare '思考')."""
    assert (
        history._compute_thinking_duration(None, "2026-07-06T12:38:23.000Z")
        is None
    )


def test_compute_thinking_duration_no_cur() -> None:
    """Missing current timestamp -> None, never crash."""
    assert (
        history._compute_thinking_duration("2026-07-06T12:38:00.000Z", None)
        is None
    )


def test_compute_thinking_duration_zero_is_none() -> None:
    """Same timestamp (0s delta) -> None, not 0 (avoid 'Thought for 0s')."""
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:00.000Z", "2026-07-06T12:38:00.000Z"
        )
        is None
    )


def test_compute_thinking_duration_negative_is_none() -> None:
    """Clock skew (negative delta) -> None."""
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:30.000Z", "2026-07-06T12:38:00.000Z"
        )
        is None
    )


def test_compute_thinking_duration_clamps_to_one() -> None:
    """Sub-second positive delta rounds up to 1 (matches live reducer clamp)."""
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:00.000Z", "2026-07-06T12:38:00.600Z"
        )
        == 1
    )


def test_compute_thinking_duration_unparseable_is_none() -> None:
    """Garbage timestamps -> None, never raise."""
    assert history._compute_thinking_duration("garbage", "2026-07-06T12:38:00Z") is None
    assert history._compute_thinking_duration(None, None) is None


def test_parse_history_thinking_duration_from_prev_entry(fake_projects: Path) -> None:
    """A thinking entry 23s after the previous entry stamps duration=23."""
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:38:00.000Z", "hi"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:38:23.000Z",
                [{"type": "thinking", "thinking": "reasoning"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds == 23


def test_parse_history_thinking_first_entry_has_no_duration(
    fake_projects: Path,
) -> None:
    """When thinking is the first timestamped entry, there is no prev -> None."""
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            _ts_entry(
                "assistant",
                "2026-07-06T12:38:23.000Z",
                [{"type": "thinking", "thinking": "reasoning"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds is None


def test_parse_history_consecutive_thinkings_each_stamped(
    fake_projects: Path,
) -> None:
    """Two consecutive thinking entries: each duration is vs its own prev entry."""
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:00:00.000Z", "hi"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:10.000Z",
                [{"type": "thinking", "thinking": "a"}],
            ),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:15.000Z",
                [{"type": "thinking", "thinking": "b"}],
            ),
        ],
    )
    thinkings = [
        e for e in history.parse_history("/workdir", "abc") if isinstance(e, ThinkingEvent)
    ]
    assert thinkings[0].thinking_duration_seconds == 10
    assert thinkings[1].thinking_duration_seconds == 5


def test_parse_history_thinking_missing_timestamp_no_duration(
    fake_projects: Path,
) -> None:
    """A jsonl entry missing its timestamp field -> duration None, no crash."""
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "x"}],
                },
            },
        ],
    )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds is None


def test_parse_history_thinking_prev_skips_unparseable_line(
    fake_projects: Path,
) -> None:
    """A garbage line between user and thinking must not reset prev_ts: the
    thinking's duration is still measured from the user entry."""
    target = fake_projects / "abc.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(_ts_entry("user", "2026-07-06T12:00:00.000Z", "hi")) + "\n")
        fh.write("not json at all\n")
        fh.write(
            json.dumps(
                _ts_entry(
                    "assistant",
                    "2026-07-06T12:00:20.000Z",
                    [{"type": "thinking", "thinking": "x"}],
                )
            )
            + "\n"
        )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds == 20
