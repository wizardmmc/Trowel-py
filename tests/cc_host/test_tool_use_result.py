"""测试样例 shape 来自已确认的 CC jsonl toolUseResult 录制。"""

from __future__ import annotations

from trowel_py.cc_host.tool_use_result import write_diff_from_cc_result
from trowel_py.schemas.cc_host import DiffHunk, WriteDiff


EDIT_TUR = {
    "filePath": "/a/service.py",
    "oldString": "    if not sid: return\n",
    "newString": "    if not sid:\n        return\n",
    "originalFile": "async def send(self, text):\n    sid = get().activeSid\n    if not sid: return\n    turn = build_turn(text)\n",
    "structuredPatch": [
        {
            "oldStart": 360,
            "oldLines": 2,
            "newStart": 360,
            "newLines": 3,
            "lines": [
                " async def send(self, text):",
                "-    if not sid: return",
                "+    if not sid:",
                "+        return",
            ],
        }
    ],
    "userModified": False,
    "replaceAll": False,
}

WRITE_UPDATE_TUR = {
    "type": "update",
    "filePath": "/a/proxy.py",
    "content": "new\n",
    "originalFile": "old\n",
    "structuredPatch": [
        {
            "oldStart": 1,
            "oldLines": 1,
            "newStart": 1,
            "newLines": 1,
            "lines": ["-old", "+new"],
        }
    ],
    "userModified": False,
}

WRITE_CREATE_TUR = {
    "type": "create",
    "filePath": "/a/new.py",
    "content": "a\nb\nc\n",
    "originalFile": "",
    "structuredPatch": [],
    "userModified": False,
}

BASH_TUR = {
    "stdout": "ok\n",
    "interrupted": False,
    "sandbox": False,
}


def test_edit_tur_becomes_update_with_real_line_numbers() -> None:
    wd = write_diff_from_cc_result(EDIT_TUR)
    assert wd is not None
    assert wd.type == "update"
    assert len(wd.hunks) == 1
    h = wd.hunks[0]
    assert h.oldStart == 360
    assert h.newStart == 360
    assert h.lines == (
        " async def send(self, text):",
        "-    if not sid: return",
        "+    if not sid:",
        "+        return",
    )


def test_write_update_tur_becomes_update() -> None:
    wd = write_diff_from_cc_result(WRITE_UPDATE_TUR)
    assert wd is not None
    assert wd.type == "update"
    assert wd.hunks[0].oldStart == 1
    assert wd.hunks[0].lines == ("-old", "+new")


def test_write_create_tur_becomes_create_with_empty_hunks() -> None:
    wd = write_diff_from_cc_result(WRITE_CREATE_TUR)
    assert wd is not None
    assert wd.type == "create"
    assert wd.hunks == ()


def test_non_edit_write_tur_returns_none() -> None:
    assert write_diff_from_cc_result(BASH_TUR) is None


def test_missing_structured_patch_returns_none() -> None:
    assert write_diff_from_cc_result({"filePath": "/a"}) is None


def test_empty_structured_patch_without_create_type_returns_none() -> None:
    assert write_diff_from_cc_result({"structuredPatch": []}) is None


def test_returns_write_diff_schema_instance() -> None:
    wd = write_diff_from_cc_result(EDIT_TUR)
    assert isinstance(wd, WriteDiff)
    assert isinstance(wd.hunks[0], DiffHunk)


def test_non_dict_input_returns_none() -> None:
    assert write_diff_from_cc_result(None) is None  # type: ignore[arg-type]
    assert write_diff_from_cc_result("not a dict") is None  # type: ignore[arg-type]


def test_multi_hunk_patch_preserves_all_hunks_and_real_lines() -> None:
    tur = {
        "structuredPatch": [
            {
                "oldStart": 10,
                "oldLines": 1,
                "newStart": 10,
                "newLines": 1,
                "lines": ["-a", "+A"],
            },
            {
                "oldStart": 500,
                "oldLines": 1,
                "newStart": 501,
                "newLines": 1,
                "lines": ["-b", "+B"],
            },
        ]
    }
    wd = write_diff_from_cc_result(tur)
    assert wd is not None
    assert wd.type == "update"
    assert [h.oldStart for h in wd.hunks] == [10, 500]
    assert [h.newStart for h in wd.hunks] == [10, 501]
