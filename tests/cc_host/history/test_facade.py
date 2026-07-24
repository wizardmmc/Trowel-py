from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, Callable, cast

import pytest

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import TrowelEvent, UserEvent


_ORIGINAL_SYMBOLS = frozenset(
    """
    Any ElicitationRequestEvent FinishedEvent Path SessionStartedEvent
    TextEvent ThinkingEvent ToolCallEvent ToolResultEvent TrowelEvent UserEvent
    WorkflowTreeEvent _COMMAND_ARGS_RE _COMMAND_NAME_RE _SKILL_TRIGGER_RE
    _clean_user_text _close_pending_turn _compute_thinking_duration
    _is_safe_session_id _load_workflow_snapshots _parse_iso_ts
    _translate_assistant _translate_line _translate_user _ts_delta_seconds
    annotations cc_projects_root datetime json logger logging parse_history
    parse_workflow_tree re workdir_to_slug write_diff_from_cc_result
    """.split()
)


def test_facade_keeps_original_symbols_and_signatures() -> None:
    assert _ORIGINAL_SYMBOLS <= vars(history).keys()
    expected_signatures: dict[Callable[..., Any], str] = {
        history._is_safe_session_id: "(cc_session_id: 'str') -> 'bool'",
        history._parse_iso_ts: "(ts: 'Any') -> 'datetime | None'",
        history._ts_delta_seconds: ("(prev_ts: 'Any', cur_ts: 'Any') -> 'int | None'"),
        history._compute_thinking_duration: (
            "(prev_ts: 'Any', thinking_ts: 'Any') -> 'int | None'"
        ),
        history._close_pending_turn: (
            "(events: 'list[TrowelEvent]', pending: 'dict[str, Any] | None') -> 'None'"
        ),
        history.parse_history: (
            "(workdir: 'str', cc_session_id: 'str') -> 'list[TrowelEvent]'"
        ),
        history._load_workflow_snapshots: (
            "(transcript_dir: 'Path') -> 'list[WorkflowTreeEvent]'"
        ),
        history._translate_line: (
            "(ev: 'dict[str, Any]', prev_ts: 'str | None') -> 'list[TrowelEvent]'"
        ),
        history._clean_user_text: "(text: 'str') -> 'str'",
        history._translate_user: ("(ev: 'dict[str, Any]') -> 'list[TrowelEvent]'"),
        history._translate_assistant: (
            "(ev: 'dict[str, Any]', prev_ts: 'str | None') -> 'list[TrowelEvent]'"
        ),
    }
    assert {
        function: str(inspect.signature(function)) for function in expected_signatures
    } == expected_signatures
    assert {function.__module__ for function in expected_signatures} == {
        "trowel_py.cc_host.history"
    }
    assert history.logger.name == "trowel_py.cc_host.history"
    assert history._COMMAND_NAME_RE.pattern == (
        r"<command-name>\s*/?\s*(\S+?)\s*</command-name>"
    )
    assert history._COMMAND_ARGS_RE.pattern == (r"<command-args>(.*?)</command-args>")
    assert history._SKILL_TRIGGER_RE.pattern == (
        r"^Use the Skill tool with skill='([^']+)'\.\s*(.*)$"
    )
    assert not history._COMMAND_NAME_RE.flags & re.DOTALL
    assert history._COMMAND_ARGS_RE.flags & re.DOTALL
    assert history._SKILL_TRIGGER_RE.flags & re.DOTALL


def test_parse_history_uses_current_facade_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_file = tmp_path / "slug" / "session.jsonl"
    session_file.parent.mkdir()
    session_file.write_text(
        '{"timestamp":"2026-07-23T12:00:00Z"}\n',
        encoding="utf-8",
    )
    loaded_paths: list[Path] = []
    pending_values: list[dict[str, Any] | None] = []

    def load_workflows(path: Path) -> list[Any]:
        loaded_paths.append(path)
        return []

    monkeypatch.setattr(history, "workdir_to_slug", lambda _: "slug")
    monkeypatch.setattr(history, "cc_projects_root", lambda: tmp_path)
    monkeypatch.setattr(
        history,
        "_load_workflow_snapshots",
        load_workflows,
    )
    monkeypatch.setattr(
        history,
        "_translate_line",
        lambda event, prev_ts: [UserEvent(text="translated")],
    )

    def close_turn(
        events: list[TrowelEvent],
        pending: dict[str, Any] | None,
    ) -> None:
        pending_values.append(pending)

    monkeypatch.setattr(history, "_close_pending_turn", close_turn)

    assert history.parse_history("/workspace", "session") == [
        UserEvent(text="translated")
    ]
    assert loaded_paths == [tmp_path / "slug" / "session"]
    assert pending_values == [
        None,
        {
            "user_idx": 0,
            "user_ts": "2026-07-23T12:00:00Z",
            "last_ts": "2026-07-23T12:00:00Z",
        },
    ]


def test_line_dispatch_uses_current_facade_translators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        history,
        "_translate_user",
        lambda event: cast(list[TrowelEvent], ["user"]),
    )
    monkeypatch.setattr(
        history,
        "_translate_assistant",
        lambda event, prev_ts: cast(
            list[TrowelEvent],
            [("assistant", prev_ts)],
        ),
    )

    assert history._translate_line({"type": "user"}, None) == ["user"]
    assert history._translate_line(
        {"type": "assistant"},
        "previous",
    ) == [("assistant", "previous")]


def test_message_wrappers_use_current_facade_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(history, "_clean_user_text", str.upper)
    monkeypatch.setattr(
        history,
        "UserEvent",
        lambda **values: ("user", values),
    )
    assert history._translate_user({"message": {"content": "hello"}}) == [
        ("user", {"text": "HELLO"})
    ]

    monkeypatch.setattr(
        history,
        "write_diff_from_cc_result",
        lambda value: ("diff", value),
    )
    monkeypatch.setattr(
        history,
        "ToolResultEvent",
        lambda **values: ("result", values),
    )
    assert history._translate_user(
        {
            "toolUseResult": "raw",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool",
                        "content": "done",
                    }
                ]
            },
        }
    ) == [
        (
            "result",
            {
                "tool_use_id": "tool",
                "content": "done",
                "write_diff": ("diff", "raw"),
            },
        )
    ]

    monkeypatch.setattr(
        history,
        "_compute_thinking_duration",
        lambda previous, current: 37,
    )
    for name, tag in (
        ("TextEvent", "text"),
        ("ThinkingEvent", "thinking"),
        ("ElicitationRequestEvent", "elicitation"),
        ("ToolCallEvent", "tool"),
    ):
        monkeypatch.setattr(
            history,
            name,
            lambda _tag=tag, **values: (_tag, values),
        )
    translated = cast(
        list[tuple[str, dict[str, Any]]],
        history._translate_assistant(
            {
                "timestamp": "current",
                "message": {
                    "content": [
                        {"type": "text", "text": "answer"},
                        {"type": "thinking", "thinking": "reasoning"},
                        {
                            "type": "tool_use",
                            "id": "question",
                            "name": "AskUserQuestion",
                            "input": {"questions": []},
                        },
                        {
                            "type": "tool_use",
                            "id": "read",
                            "name": "Read",
                            "input": {"file_path": "x"},
                        },
                    ]
                },
            },
            "previous",
        ),
    )
    assert [tag for tag, _ in translated] == [
        "text",
        "thinking",
        "elicitation",
        "tool",
    ]
    assert translated[1][1]["thinking_duration_seconds"] == 37


def test_clean_user_text_uses_current_facade_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        history,
        "_COMMAND_NAME_RE",
        re.compile(r"<name>(.*?)</name>"),
    )
    monkeypatch.setattr(
        history,
        "_COMMAND_ARGS_RE",
        re.compile(r"<args>(.*?)</args>"),
    )

    assert (
        history._clean_user_text("<name>custom</name><args> value </args>")
        == "/custom value"
    )
    monkeypatch.setattr(
        history,
        "_COMMAND_NAME_RE",
        re.compile(r"never-match"),
    )
    monkeypatch.setattr(
        history,
        "_SKILL_TRIGGER_RE",
        re.compile(r"^Run ([^:]+):\s*(.*)$"),
    )
    assert history._clean_user_text("Run inspect: target") == "/inspect target"


def test_workflow_loader_uses_current_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "wf_one.json").write_text(
        '{"startTime": 1}',
        encoding="utf-8",
    )
    sentinel = cast(Any, object())
    monkeypatch.setattr(
        history,
        "parse_workflow_tree",
        lambda workflow: sentinel,
    )

    assert history._load_workflow_snapshots(tmp_path) == [sentinel]
