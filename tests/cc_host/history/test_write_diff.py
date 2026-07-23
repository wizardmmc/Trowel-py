from __future__ import annotations

from pathlib import Path

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import ToolResultEvent
from tests.cc_host.history._support import (
    _assistant,
    _tool_result,
    _tool_result_with_tur,
    _user_text,
    _write_jsonl,
)

# structuredPatch 的行号来自真实 CC 记录，回放不能把它重排为局部补丁行号。


def test_parse_history_attaches_edit_write_diff_with_real_lines(
    fake_projects: Path,
) -> None:
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
            _tool_result("call_b1", "hi"),
        ],
    )

    events = history.parse_history("/workdir", "abc-123")
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.write_diff is None
