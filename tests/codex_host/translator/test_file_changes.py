from __future__ import annotations


from trowel_py.codex_host.events import (
    CodexEventType,
)
from trowel_py.codex_host.translator import CodexTranslator
from tests.codex_host.translator._support import (
    _file_change_msg,
)


def test_file_change_started_add_maps_to_tool_started_with_create_diff() -> None:

    # fixture 来自 Codex 0.144.0 在 workspace-write/never 下的真实 apply_patch。
    msg = _file_change_msg("file-change-add-modify-076.jsonl", "item/started", "add")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_STARTED
    assert item.payload["kind"] == "fileChange"
    assert item.payload["status"] == "inProgress"
    change = item.payload["changes"][0]
    assert change["change_kind"] == "add"
    assert change["path"].endswith("hello.txt")
    wd = change["write_diff"]
    assert wd["type"] == "create"

    assert len(wd["hunks"]) == 1
    assert wd["hunks"][0]["newStart"] == 1
    assert wd["hunks"][0]["newLines"] == 2
    assert wd["hunks"][0]["lines"] == ("+hello", "+world")


def test_file_change_completed_update_parses_unified_diff_into_hunks() -> None:

    msg = _file_change_msg(
        "file-change-add-modify-076.jsonl", "item/completed", "update"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    assert item.payload["status"] == "completed"
    change = item.payload["changes"][0]
    assert change["change_kind"] == "modify"
    assert change["move_path"] is None
    wd = change["write_diff"]
    assert wd["type"] == "update"
    assert len(wd["hunks"]) == 1
    hunk = wd["hunks"][0]
    assert hunk["oldStart"] == 1
    assert hunk["oldLines"] == 1
    assert hunk["newStart"] == 1
    assert hunk["newLines"] == 1
    assert hunk["lines"] == ("-hi", "+hey")


def test_file_change_delete_maps_to_delete_write_diff() -> None:

    msg = _file_change_msg("file-change-delete-076.jsonl", "item/completed", "delete")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    change = item.payload["changes"][0]
    assert change["change_kind"] == "delete"
    wd = change["write_diff"]
    assert wd["type"] == "delete"

    assert len(wd["hunks"]) == 1
    assert wd["hunks"][0]["oldStart"] == 1
    assert wd["hunks"][0]["oldLines"] == 2
    assert wd["hunks"][0]["lines"] == ("-hello", "-world")


def test_parse_unified_diff_single_line_hunk() -> None:

    from trowel_py.codex_host.translator import _parse_unified_diff

    hunks = _parse_unified_diff("@@ -1 +1 @@\n-hi\n+hey\n")
    assert len(hunks) == 1
    assert hunks[0]["oldStart"] == 1
    assert hunks[0]["oldLines"] == 1
    assert hunks[0]["newStart"] == 1
    assert hunks[0]["newLines"] == 1
    assert hunks[0]["lines"] == ("-hi", "+hey")


def test_parse_unified_diff_multi_line_hunk_with_context() -> None:

    from trowel_py.codex_host.translator import _parse_unified_diff

    patch = "@@ -1,3 +1,3 @@\n keep\n-old\n+new\n keep2\n"
    hunks = _parse_unified_diff(patch)
    assert len(hunks) == 1
    h = hunks[0]
    assert h["oldStart"] == 1
    assert h["oldLines"] == 3
    assert h["newStart"] == 1
    assert h["newLines"] == 3
    assert h["lines"] == (" keep", "-old", "+new", " keep2")


def test_parse_unified_diff_multiple_hunks() -> None:

    from trowel_py.codex_host.translator import _parse_unified_diff

    patch = "@@ -1 +1 @@\n-a\n+b\n@@ -10 +10 @@\n-c\n+d\n"
    hunks = _parse_unified_diff(patch)
    assert len(hunks) == 2
    assert hunks[0]["oldStart"] == 1
    assert hunks[1]["oldStart"] == 10


def test_file_change_declined_preserves_declined_status() -> None:

    msg = _file_change_msg("file-change-declined-076.jsonl", "item/completed", "add")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    assert item.payload["status"] == "declined"
    change = item.payload["changes"][0]
    assert change["change_kind"] == "add"

    # declined 仍携带拟议 diff 供 UI 展示，status 才表示它没有真正写入。
    assert change["write_diff"]["type"] == "create"
