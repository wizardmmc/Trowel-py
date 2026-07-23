from trowel_py.codex_host.events import CodexEventType

from .support import make_codex_event


def test_command_started_maps_to_tool_call(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.TOOL_STARTED,
            seq=1,
            item_id="item-3",
            payload={
                "kind": "commandExecution",
                "command": "rg pattern",
                "cwd": "/repo",
                "source": "unifiedExecStartup",
                "command_actions": (
                    {
                        "type": "search",
                        "command": "rg pattern",
                        "query": "pattern",
                        "path": ".",
                    },
                ),
                "started_at": 1234,
            },
        )
    )

    assert event.type == "tool_call"
    assert event.item_id == "item-3"
    assert event.payload["tool_use_id"] == "item-3"
    assert event.payload["tool_name"] == "command"
    assert event.payload["input"] == {
        "command": "rg pattern",
        "cwd": "/repo",
        "source": "unifiedExecStartup",
        "command_actions": [
            {
                "type": "search",
                "command": "rg pattern",
                "query": "pattern",
                "path": ".",
            }
        ],
    }


def test_command_completed_maps_result_fields(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.TOOL_COMPLETED,
            seq=2,
            item_id="item-3",
            payload={
                "kind": "commandExecution",
                "command": "rg pattern",
                "cwd": "/repo",
                "status": "completed",
                "exit_code": 0,
                "output": "match.txt:1:hit",
                "duration_ms": 12,
                "completed_at": 5678,
            },
        )
    )

    assert event.type == "tool_result"
    assert event.item_id == "item-3"
    assert event.payload["tool_use_id"] == "item-3"
    assert event.payload["content"] == "match.txt:1:hit"
    assert event.payload["exit_code"] == 0
    assert event.payload["duration_ms"] == 12
    assert event.payload["cwd"] == "/repo"


def test_file_change_started_maps_apply_patch_targets(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.TOOL_STARTED,
            seq=3,
            item_id="item-fc",
            payload={
                "kind": "fileChange",
                "changes": (
                    {
                        "path": "/repo/hello.txt",
                        "change_kind": "add",
                        "move_path": None,
                        "write_diff": {"type": "create", "hunks": ()},
                    },
                ),
                "status": "inProgress",
                "started_at": 1234,
            },
        )
    )

    assert event.type == "tool_call"
    assert event.item_id == "item-fc"
    assert event.payload["tool_use_id"] == "item-fc"
    assert event.payload["tool_name"] == "apply_patch"
    assert event.payload["input"] == {
        "paths": ["/repo/hello.txt"],
        "change_kinds": ["add"],
    }


def test_file_change_completed_maps_diff_fields(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.TOOL_COMPLETED,
            seq=4,
            item_id="item-fc",
            payload={
                "kind": "fileChange",
                "changes": (
                    {
                        "path": "/repo/greeting.txt",
                        "change_kind": "modify",
                        "move_path": None,
                        "write_diff": {
                            "type": "update",
                            "hunks": (
                                {
                                    "oldStart": 1,
                                    "oldLines": 1,
                                    "newStart": 1,
                                    "newLines": 1,
                                    "lines": ("-hi", "+hey"),
                                },
                            ),
                        },
                    },
                ),
                "status": "completed",
                "completed_at": 5678,
            },
        )
    )

    assert event.type == "tool_result"
    assert event.item_id == "item-fc"
    assert event.payload["tool_use_id"] == "item-fc"
    assert event.payload["change_kind"] == "modify"
    assert event.payload["path"] == "/repo/greeting.txt"
    assert event.payload["status"] == "completed"
    write_diff = event.payload["write_diff"]
    assert write_diff["type"] == "update"
    assert len(write_diff["hunks"]) == 1
    assert list(write_diff["hunks"][0]["lines"]) == ["-hi", "+hey"]


def test_declined_file_change_keeps_declined_status(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.TOOL_COMPLETED,
            seq=5,
            item_id="item-fc",
            payload={
                "kind": "fileChange",
                "changes": (
                    {
                        "path": "/repo/x.txt",
                        "change_kind": "add",
                        "move_path": None,
                        "write_diff": {"type": "create", "hunks": ()},
                    },
                ),
                "status": "declined",
                "completed_at": 5678,
            },
        )
    )

    assert event.type == "tool_result"
    assert event.payload["status"] == "declined"
