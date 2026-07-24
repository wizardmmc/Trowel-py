from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.file_change_codec import (
    HUNK_HEADER,
    file_change_to_change,
    file_change_write_diff,
    full_file_hunk,
    parse_unified_diff,
)


def write_diff(kind_type: Any, diff: Any) -> dict[str, Any]:
    return file_change_write_diff(
        kind_type,
        diff,
        add_type="add",
        delete_type="delete",
        update_type="update",
        full_file_hunk_fn=full_file_hunk,
        parse_unified_diff_fn=lambda patch: parse_unified_diff(
            patch,
            hunk_header=HUNK_HEADER,
        ),
        protocol_violation_type=ProtocolViolationError,
    )


def change_props(change: Mapping[str, Any], method: str) -> dict[str, Any]:
    return file_change_to_change(
        change,
        method,
        add_type="add",
        delete_type="delete",
        update_type="update",
        mapping_type=Mapping,
        as_str=lambda value: value if isinstance(value, str) else str(value),
        write_diff_fn=write_diff,
        protocol_violation_type=ProtocolViolationError,
    )


def test_parse_unified_diff_preserves_hunk_defaults_order_and_markers() -> None:
    patch = (
        "--- a/example.txt\n"
        "+++ b/example.txt\n"
        "@@ -1 +2 @@ label\n"
        "-old\n"
        "+new\n"
        "\\ No newline at end of file\n"
        "@@ -10,2 +11,3 @@\n"
        " context\n"
        "-gone\n"
        "+first\n"
        "+second\n"
    )

    assert parse_unified_diff(patch, hunk_header=HUNK_HEADER) == (
        {
            "oldStart": 1,
            "oldLines": 1,
            "newStart": 2,
            "newLines": 1,
            "lines": ("-old", "+new"),
        },
        {
            "oldStart": 10,
            "oldLines": 2,
            "newStart": 11,
            "newLines": 3,
            "lines": (" context", "-gone", "+first", "+second"),
        },
    )


def test_parse_unified_diff_without_hunk_header_is_empty() -> None:
    assert parse_unified_diff("plain\n+text\n", hunk_header=HUNK_HEADER) == ()


@pytest.mark.parametrize(
    ("text", "marker", "expected"),
    [
        (
            "first\nsecond\n",
            "+",
            (
                {
                    "oldStart": 0,
                    "oldLines": 0,
                    "newStart": 1,
                    "newLines": 2,
                    "lines": ("+first", "+second"),
                },
            ),
        ),
        (
            "first\nsecond\n",
            "-",
            (
                {
                    "oldStart": 1,
                    "oldLines": 2,
                    "newStart": 0,
                    "newLines": 0,
                    "lines": ("-first", "-second"),
                },
            ),
        ),
        ("", "+", ()),
    ],
)
def test_full_file_hunk_preserves_existing_shapes(
    text: str,
    marker: str,
    expected: tuple[dict[str, Any], ...],
) -> None:
    assert full_file_hunk(text, marker) == expected


def test_write_diff_keeps_full_content_and_unified_diff_paths() -> None:
    assert write_diff("add", "one\n") == {
        "type": "create",
        "hunks": full_file_hunk("one\n", "+"),
    }
    assert write_diff("delete", "one\n") == {
        "type": "delete",
        "hunks": full_file_hunk("one\n", "-"),
    }
    assert write_diff("update", "@@ -1 +1 @@\n-old\n+new\n") == {
        "type": "update",
        "hunks": (
            {
                "oldStart": 1,
                "oldLines": 1,
                "newStart": 1,
                "newLines": 1,
                "lines": ("-old", "+new"),
            },
        ),
    }


def test_write_diff_unknown_kind_keeps_error_message_and_payload() -> None:
    with pytest.raises(
        ProtocolViolationError,
        match=r"fileChange write_diff: unexpected kind type 'future'",
    ) as caught:
        write_diff("future", "content")

    assert caught.value.payload == {"kind_type": "future"}


def test_change_props_preserves_move_path_spelling_and_path_conversion() -> None:
    rename = change_props(
        {
            "path": 42,
            "kind": {"type": "update", "movePath": "renamed.txt"},
            "diff": "",
        },
        "item/completed",
    )
    snake_case = change_props(
        {
            "path": None,
            "kind": {"type": "update", "move_path": "ignored.txt"},
            "diff": None,
        },
        "item/completed",
    )

    assert rename["path"] == "42"
    assert rename["change_kind"] == "rename"
    assert rename["move_path"] == "renamed.txt"
    assert snake_case["path"] == ""
    assert snake_case["change_kind"] == "modify"
    assert snake_case["move_path"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"kind": None},
            "notification 'item/started' fileChange change.kind is not an object",
        ),
        (
            {"kind": {"type": "future"}},
            "notification 'item/started' fileChange change.kind.type has "
            "unexpected value 'future'",
        ),
    ],
)
def test_change_props_preserves_protocol_errors(
    change: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ProtocolViolationError, match=message) as caught:
        change_props(change, "item/started")

    assert caught.value.payload == change
