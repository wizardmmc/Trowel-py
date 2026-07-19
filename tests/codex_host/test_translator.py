"""Translator unit tests — pin notification → CodexEvent mapping.

Every input here is either a line from the real 2026-07-18 fixture recordings
(``fixtures/notifications.jsonl``) or a notification constructed strictly from
the Rust protocol types in ``app-server-protocol/src/protocol/v2/`` at Codex
0.144.0 (clearly marked with ``# source:`` comments). No field is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import CodexEventType, HostStatusKind, host_status_item
from trowel_py.codex_host.translator import CodexTranslator

_FIXTURES = Path(__file__).parent / "fixtures"


def _notifications() -> list[dict]:
    """Load the real recorded notifications fixture."""

    return [
        json.loads(line)
        for line in (_FIXTURES / "notifications.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _command_action_notifications() -> list[dict]:
    """Load the sanitized 2026-07-19 list/read/search probe recording."""

    return [
        json.loads(line)
        for line in (_FIXTURES / "command-actions.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _by_method(method: str) -> dict:
    """Return the first recorded notification with the given method."""

    for msg in _notifications():
        if msg["method"] == method:
            return msg
    raise AssertionError(f"no notification with method {method!r} in fixture")


# --------------------------------------------------------------- real fixture


def test_agent_message_delta_translates_to_assistant_delta() -> None:
    """item/agentMessage/delta → ASSISTANT_DELTA with stable item id."""

    msg = _by_method("item/agentMessage/delta")
    items = CodexTranslator().translate(msg["method"], msg["params"])
    assert len(items) == 1
    item = items[0]
    assert item.type is CodexEventType.ASSISTANT_DELTA
    assert item.thread_id == msg["params"]["threadId"]
    assert item.turn_id == msg["params"]["turnId"]
    assert item.item_id == msg["params"]["itemId"]
    assert item.payload["delta"] == msg["params"]["delta"]


def test_command_started_translates_to_tool_started() -> None:
    """item/started(commandExecution) → TOOL_STARTED with command + cwd."""

    msg = _by_method("item/started")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_STARTED
    native_item = msg["params"]["item"]
    assert item.item_id == native_item["id"]
    assert item.payload["command"] == native_item["command"]
    assert item.payload["cwd"] == native_item["cwd"]
    assert item.payload["started_at"] == msg["params"]["startedAtMs"]


def test_command_completed_translates_to_tool_completed() -> None:
    """item/completed(commandExecution) → TOOL_COMPLETED with exit/output/duration."""

    msg = next(
        m for m in _notifications()
        if m["method"] == "item/completed" and m["params"]["item"]["type"] == "commandExecution"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    native_item = msg["params"]["item"]
    assert item.payload["exit_code"] == native_item["exitCode"]
    assert item.payload["output"] == native_item["aggregatedOutput"]
    assert item.payload["duration_ms"] == native_item["durationMs"]
    assert item.payload["status"] == "completed"


@pytest.mark.parametrize("method", ["item/started", "item/completed"])
@pytest.mark.parametrize("item_id", ["<list-item>", "<read-item>", "<search-item>"])
def test_command_actions_and_source_survive_translation(
    method: str, item_id: str
) -> None:
    """Native 0.144.0 commandActions/source cross the translator unchanged."""

    msg = next(
        row
        for row in _command_action_notifications()
        if row["method"] == method and row["params"]["item"]["id"] == item_id
    )
    native = msg["params"]["item"]
    translated = CodexTranslator().translate(method, msg["params"])[0]
    assert translated.payload["source"] == "unifiedExecStartup"
    assert translated.payload["command_actions"] == tuple(native["commandActions"])


def test_agent_message_completed_translates_to_assistant_message() -> None:
    """item/completed(agentMessage) → ASSISTANT_MESSAGE with full text + phase."""

    msg = next(
        m for m in _notifications()
        if m["method"] == "item/completed" and m["params"]["item"]["type"] == "agentMessage"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.ASSISTANT_MESSAGE
    native_item = msg["params"]["item"]
    assert item.payload["text"] == native_item["text"]
    assert item.payload["phase"] == native_item["phase"]


def test_token_usage_translates_to_usage_updated() -> None:
    """thread/tokenUsage/updated → USAGE_UPDATED preserving total/last/window."""

    msg = _by_method("thread/tokenUsage/updated")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.USAGE_UPDATED
    usage = msg["params"]["tokenUsage"]
    assert item.payload["total"] == usage["total"]
    assert item.payload["last"] == usage["last"]
    assert item.payload["model_context_window"] == usage["modelContextWindow"]


def test_turn_completed_status_completed_translates_to_finished() -> None:
    """turn/completed with status=completed → FINISHED (not a generic finish)."""

    msg = _by_method("turn/completed")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.FINISHED
    assert item.turn_id == msg["params"]["turn"]["id"]
    assert item.payload["status"] == "completed"
    assert item.payload["duration_ms"] == msg["params"]["turn"]["durationMs"]


def test_thread_status_changed_translates_to_status() -> None:
    """thread/status/changed → STATUS with type + active flags tuple."""

    msg = _by_method("thread/status/changed")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.STATUS
    assert item.payload["status"] == msg["params"]["status"]["type"]
    assert item.payload["active_flags"] == tuple(
        msg["params"]["status"]["activeFlags"]
    )


# ------------------------------------------- source-grounded boundary cases
# These notifications are NOT in the spike fixture. Their shapes come straight
# from the 0.144.0 Rust protocol types; ``# source:`` cites the struct.


def test_turn_completed_interrupted_translates_to_interrupted() -> None:
    """turn/completed.status=interrupted → INTERRUPTED.

    # source: v2/turn.rs::TurnStatus::Interrupted (spike confirmed via probe).
    """

    params = {
        "threadId": "t-1",
        "turn": {"id": "turn-9", "status": "interrupted", "durationMs": 120},
    }
    item = CodexTranslator().translate("turn/completed", params)[0]
    assert item.type is CodexEventType.INTERRUPTED
    assert item.turn_id == "turn-9"
    assert item.payload["status"] == "interrupted"


def test_turn_completed_failed_translates_to_error() -> None:
    """turn/completed.status=failed → ERROR (turn-level failure).

    # source: v2/turn.rs::TurnStatus::Failed.
    """

    params = {
        "threadId": "t-1",
        "turn": {"id": "turn-9", "status": "failed", "error": {"message": "boom"}},
    }
    item = CodexTranslator().translate("turn/completed", params)[0]
    assert item.type is CodexEventType.ERROR
    assert item.payload["status"] == "failed"
    assert item.payload["error"] == {"message": "boom"}


def test_reasoning_text_delta_translates_to_reasoning_delta() -> None:
    """item/reasoning/textDelta → REASONING_DELTA with content_index.

    # source: schema-baseline-0.144.0.txt lists ``item/reasoning/textDelta``
    # and ``item/reasoning/summaryTextDelta``; struct shape from
    # v2/item.rs::ReasoningTextDeltaNotification (not in the spike recording).
    """

    params = {
        "threadId": "t-1",
        "turnId": "turn-1",
        "itemId": "rsn_abc",
        "delta": "thinking...",
        "contentIndex": 0,
    }
    item = CodexTranslator().translate("item/reasoning/textDelta", params)[0]
    assert item.type is CodexEventType.REASONING_DELTA
    assert item.item_id == "rsn_abc"
    assert item.payload["delta"] == "thinking..."
    assert item.payload["content_index"] == 0


def test_error_notification_translates_to_error_with_will_retry() -> None:
    """``error`` notification → ERROR surfacing will_retry.

    # source: v2/notification.rs::ErrorNotification.
    """

    params = {
        "threadId": "t-1",
        "turnId": "turn-1",
        "error": {"type": "rate_limit", "message": "slow down"},
        "willRetry": True,
    }
    item = CodexTranslator().translate("error", params)[0]
    assert item.type is CodexEventType.ERROR
    assert item.payload["will_retry"] is True
    assert item.payload["error_type"] == "rate_limit"


# ---------------------------------------------------- unknown / drift cases


def test_unknown_method_returns_empty() -> None:
    """An unmapped method yields no items; the manager records diagnostics."""

    assert CodexTranslator().translate("some/future/method", {"threadId": "t"}) == []


def test_ignored_methods_return_empty() -> None:
    """Capability-gated methods listed in _IGNORED_METHODS translate to nothing."""

    translator = CodexTranslator()
    assert translator.translate("thread/started", {"thread": {"id": "t"}}) == []
    assert translator.translate("turn/started", {"threadId": "t", "turn": {"id": "x"}}) == []
    assert translator.translate("account/rateLimits/updated", {}) == []
    assert translator.translate("mcpServer/startupStatus/updated", {}) == []


def test_missing_required_field_raises_protocol_violation() -> None:
    """A mapped notification missing a documented field is drift, not silent."""

    translator = CodexTranslator()
    with pytest.raises(ProtocolViolationError):
        translator.translate("item/agentMessage/delta", {"threadId": "t"})  # no turnId/itemId/delta


def test_unexpected_turn_status_raises_protocol_violation() -> None:
    """turn/completed.status=inProgress means the shape changed — fail loudly."""

    params = {
        "threadId": "t-1",
        "turn": {"id": "turn-9", "status": "inProgress"},
    }
    with pytest.raises(ProtocolViolationError):
        CodexTranslator().translate("turn/completed", params)


# ---------------------------------------------------------- host_status helper


def test_host_status_item_carries_status_and_reason() -> None:
    """The synthesised host_status item is addressed to a single thread."""

    item = host_status_item(
        HostStatusKind.HOST_EXITED, thread_id="t-1", reason="eof", exit_code=1
    )
    assert item.type is CodexEventType.HOST_STATUS
    assert item.thread_id == "t-1"
    assert item.payload["status"] == "host_exited"
    assert item.payload["reason"] == "eof"
    assert item.payload["exit_code"] == 1


def test_full_fixture_replay_produces_no_protocol_errors() -> None:
    """Replaying the whole fixture must not raise — every recorded notification
    is structurally valid against the 0.144.0 schema."""

    translator = CodexTranslator()
    emitted = 0
    for msg in _notifications():
        emitted += len(translator.translate(msg["method"], msg["params"]))
    # The fixture has 7 translatable notifications (the rest are echoes /
    # capability-gated: thread/started, turn/started, rateLimits, mcp, resolved).
    assert emitted >= 7


# ----------------------------------------------------- file change (slice-076)
# Real fixtures: file-change-add-modify-076.jsonl (add + add + update on two
# files), file-change-delete-076.jsonl (delete). Recorded 2026-07-19 against
# Codex 0.144.0 apply_patch under sandbox=workspace-write, approvalPolicy=never.


def _file_change_notifications(name: str) -> list[dict]:
    """Load one of the 2026-07-19 file-change probe recordings."""

    return [
        json.loads(line)
        for line in (_FIXTURES / name).read_text().splitlines()
        if line.strip()
    ]


def _file_change_msg(name: str, method: str, kind_type: str) -> dict:
    """Pick the first fileChange notification of ``method`` whose first change
    matches ``kind_type`` (add/delete/update)."""

    for msg in _file_change_notifications(name):
        if msg["method"] != method:
            continue
        changes = msg["params"]["item"]["changes"]
        if changes and changes[0]["kind"]["type"] == kind_type:
            return msg
    raise AssertionError(
        f"no {method} fileChange with kind {kind_type!r} in {name}"
    )


def test_file_change_started_add_maps_to_tool_started_with_create_diff() -> None:
    """item/started{fileChange,add} → TOOL_STARTED, kind=fileChange, create diff."""

    msg = _file_change_msg(
        "file-change-add-modify-076.jsonl", "item/started", "add"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_STARTED
    assert item.payload["kind"] == "fileChange"
    assert item.payload["status"] == "inProgress"
    change = item.payload["changes"][0]
    assert change["change_kind"] == "add"
    assert change["path"].endswith("hello.txt")
    wd = change["write_diff"]
    assert wd["type"] == "create"
    # Add carries full file content as one all-added hunk (hello\nworld\n → 2 +lines).
    assert len(wd["hunks"]) == 1
    assert wd["hunks"][0]["newStart"] == 1
    assert wd["hunks"][0]["newLines"] == 2
    assert wd["hunks"][0]["lines"] == ("+hello", "+world")


def test_file_change_completed_update_parses_unified_diff_into_hunks() -> None:
    """item/completed{fileChange,update} → TOOL_COMPLETED with hunks parsed
    from the unified_diff string ('@@ -1 +1 @@' → one hunk, -hi/+hey)."""

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
    """item/completed{fileChange,delete} → change_kind=delete + write_diff
    type=delete (the diff field carries the removed content, not hunks)."""

    msg = _file_change_msg(
        "file-change-delete-076.jsonl", "item/completed", "delete"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    change = item.payload["changes"][0]
    assert change["change_kind"] == "delete"
    wd = change["write_diff"]
    assert wd["type"] == "delete"
    # Delete carries the removed file content as one all-removed hunk.
    assert len(wd["hunks"]) == 1
    assert wd["hunks"][0]["oldStart"] == 1
    assert wd["hunks"][0]["oldLines"] == 2
    assert wd["hunks"][0]["lines"] == ("-hello", "-world")


def test_parse_unified_diff_single_line_hunk() -> None:
    """'@@ -1 +1 @@' (count elided) parses with old/new start and +/- lines."""

    from trowel_py.codex_host.translator import _parse_unified_diff

    hunks = _parse_unified_diff("@@ -1 +1 @@\n-hi\n+hey\n")
    assert len(hunks) == 1
    assert hunks[0]["oldStart"] == 1
    assert hunks[0]["oldLines"] == 1
    assert hunks[0]["newStart"] == 1
    assert hunks[0]["newLines"] == 1
    assert hunks[0]["lines"] == ("-hi", "+hey")


def test_parse_unified_diff_multi_line_hunk_with_context() -> None:
    """'@@ -1,3 +1,3 @@' parses context (' '), removed ('-') and added ('+')."""

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
    """Two @@ headers in one patch produce two separate hunks."""

    from trowel_py.codex_host.translator import _parse_unified_diff

    patch = "@@ -1 +1 @@\n-a\n+b\n@@ -10 +10 @@\n-c\n+d\n"
    hunks = _parse_unified_diff(patch)
    assert len(hunks) == 2
    assert hunks[0]["oldStart"] == 1
    assert hunks[1]["oldStart"] == 10


def test_file_change_declined_preserves_declined_status() -> None:
    """item/completed{fileChange,status=declined} → TOOL_COMPLETED with
    status=declined. A refused patch must surface as declined, not as a
    successful write (spec: declined file item must not show as success)."""

    msg = _file_change_msg(
        "file-change-declined-076.jsonl", "item/completed", "add"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    assert item.payload["status"] == "declined"
    change = item.payload["changes"][0]
    assert change["change_kind"] == "add"
    # The proposed change shape is still carried so the UI can show what was
    # refused; the declined ``status`` is what marks it as not-written.
    assert change["write_diff"]["type"] == "create"
