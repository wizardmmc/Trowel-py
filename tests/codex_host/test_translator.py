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


def test_mcp_tool_started_translates_to_tool_started() -> None:
    """slice-078: item/started(mcpToolCall) → TOOL_STARTED with server.tool name."""

    params = {
        "threadId": "thr-1",
        "turnId": "trn-1",
        "startedAtMs": 100,
        "item": {
            "id": "call-1",
            "type": "mcpToolCall",
            "server": "trowel_note_search",
            "tool": "search",
            "arguments": {"query": "x"},
            "status": "inProgress",
        },
    }
    item = CodexTranslator().translate("item/started", params)[0]
    assert item.type is CodexEventType.TOOL_STARTED
    assert item.item_id == "call-1"
    assert item.payload["kind"] == "mcpToolCall"
    assert item.payload["server"] == "trowel_note_search"
    assert item.payload["tool"] == "search"
    assert item.payload["tool_name"] == "trowel_note_search.search"
    assert item.payload["arguments"] == {"query": "x"}
    assert item.payload["started_at"] == 100


def test_mcp_tool_completed_translates_to_tool_completed() -> None:
    """slice-078: item/completed(mcpToolCall) → TOOL_COMPLETED with result/duration."""

    result = {"content": [{"type": "text", "text": "{}"}]}
    params = {
        "threadId": "thr-1",
        "turnId": "trn-1",
        "completedAtMs": 200,
        "item": {
            "id": "call-1",
            "type": "mcpToolCall",
            "server": "trowel_note_search",
            "tool": "read",
            "arguments": {"uri": "memory://notes/x"},
            "status": "completed",
            "result": result,
            "durationMs": 50,
        },
    }
    item = CodexTranslator().translate("item/completed", params)[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    assert item.payload["tool_name"] == "trowel_note_search.read"
    assert item.payload["status"] == "completed"
    assert item.payload["result"] == result
    assert item.payload["duration_ms"] == 50
    assert item.payload["completed_at"] == 200


def test_mcp_tool_failed_preserves_status_and_error() -> None:
    """A failed MCP call (e.g. dictionary_empty) must not paint as success."""

    params = {
        "threadId": "thr-1",
        "turnId": "trn-1",
        "item": {
            "id": "call-2",
            "type": "mcpToolCall",
            "server": "trowel_note_search",
            "tool": "search",
            "status": "failed",
            "error": {"message": "dictionary_empty"},
        },
    }
    item = CodexTranslator().translate("item/completed", params)[0]
    assert item.payload["status"] == "failed"
    assert item.payload["error"] == {"message": "dictionary_empty"}


def test_mcp_tool_missing_required_field_raises_protocol_violation() -> None:
    """slice-078: id/server/tool/status are mandatory (item.rs:302) — a schema
    drift surfaces as ProtocolViolationError, not a silent fallback to 'mcp'."""

    from trowel_py.codex_host.errors import ProtocolViolationError

    params = {
        "threadId": "thr-1",
        "turnId": "trn-1",
        "item": {"id": "call-3", "type": "mcpToolCall"},  # no server/tool/status
    }
    with pytest.raises(ProtocolViolationError):
        CodexTranslator().translate("item/started", params)


def test_mcp_tool_provenance_fields_pass_through() -> None:
    """slice-078: appContext / mcpAppResourceUri / pluginId survive translation
    on both started and completed (codex review MEDIUM-2)."""

    app_context = {"connector_id": "conn-1", "app_name": "demo"}
    started_params = {
        "threadId": "thr-1",
        "turnId": "trn-1",
        "item": {
            "id": "call-9",
            "type": "mcpToolCall",
            "server": "trowel_note_search",
            "tool": "search",
            "status": "inProgress",
            "arguments": None,
            "appContext": app_context,
            "mcpAppResourceUri": "mem://x",
            "pluginId": "plug-1",
        },
    }
    started = CodexTranslator().translate("item/started", started_params)[0]
    assert started.payload["status"] == "inProgress"
    assert started.payload["app_context"] == app_context
    assert started.payload["mcp_app_resource_uri"] == "mem://x"
    assert started.payload["plugin_id"] == "plug-1"

    completed_params = {
        "threadId": "thr-1",
        "turnId": "trn-1",
        "item": {
            "id": "call-9",
            "type": "mcpToolCall",
            "server": "trowel_note_search",
            "tool": "search",
            "status": "completed",
            "arguments": None,
            "appContext": app_context,
            "mcpAppResourceUri": "mem://x",
            "pluginId": "plug-1",
            "result": {"content": []},
            "durationMs": 7,
        },
    }
    completed = CodexTranslator().translate("item/completed", completed_params)[0]
    assert completed.payload["app_context"] == app_context
    assert completed.payload["mcp_app_resource_uri"] == "mem://x"
    assert completed.payload["plugin_id"] == "plug-1"


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


# -------------------------------------------------- rate limit (slice-077)
# Real fixture: notifications.jsonl has one account/rateLimits/updated recorded
# 2026-07-18 (usedPercent 20, planType "pro", primary window only, not reached).
# Source: account.rs:518 AccountRateLimitsUpdatedNotification.


def test_rate_limits_updated_translates_to_rate_limit_updated() -> None:
    """account/rateLimits/updated -> RATE_LIMIT_UPDATED (global, no thread_id).

    The notification has no top-level threadId -- it is an account-level update
    (source: account.rs:518). Decision 5: the full snapshot is preserved in the
    payload; the UI unfolds only used_percent / resets_at / reached_type later.
    """

    msg = _by_method("account/rateLimits/updated")
    items = CodexTranslator().translate(msg["method"], msg["params"])
    assert len(items) == 1
    item = items[0]
    assert item.type is CodexEventType.RATE_LIMIT_UPDATED
    assert item.thread_id is None
    snapshot = msg["params"]["rateLimits"]
    assert item.payload["limit_id"] == snapshot["limitId"]
    assert item.payload["limit_name"] == snapshot["limitName"]
    assert item.payload["plan_type"] == snapshot["planType"]
    assert item.payload["rate_limit_reached_type"] == snapshot["rateLimitReachedType"]
    # Windows / credits survive as nested mappings so the UI can read every
    # field the protocol provides without a later schema change.
    assert item.payload["primary"] == snapshot["primary"]
    assert item.payload["secondary"] == snapshot["secondary"]
    assert item.payload["credits"] == snapshot["credits"]
    assert item.payload["individual_limit"] == snapshot["individualLimit"]
    # spend_control_reached is Optional in protocol (account.rs:534) and absent
    # in this 2026-07-18 recording -- payload surfaces the key as None, never
    # fabricated (spec C-4: usage null 诚实; same principle for sparse fields).
    assert item.payload["spend_control_reached"] is None


def test_rate_limits_missing_field_raises_protocol_violation() -> None:
    """A rate-limit update without the rateLimits object is protocol drift."""

    with pytest.raises(ProtocolViolationError):
        CodexTranslator().translate("account/rateLimits/updated", {})


def test_ignored_methods_return_empty() -> None:
    """Capability-gated methods listed in _IGNORED_METHODS translate to nothing."""

    translator = CodexTranslator()
    assert translator.translate("thread/started", {"thread": {"id": "t"}}) == []
    assert translator.translate("turn/started", {"threadId": "t", "turn": {"id": "x"}}) == []
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
    # The fixture has 8 translatable notifications (the rest are echoes /
    # capability-gated: thread/started, turn/started, mcp, resolved).
    # rate-limit joined slice-077; it now translates instead of being gated.
    assert emitted >= 8


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


# ------------------------------------------- slice-077 capability=false skeletons
# The handlers below are READY but NOT ROUTED. Inputs are constructed strictly
# from codex 0.144.0 Rust protocol types (turn.rs:426 / item.rs:359 /
# item.rs:388 / notification.rs:21) — no real fixture exists because the
# capability is not wired (todo-mcp / compact client / etc.). See
# slice-077.md §阶段2 for the activation path.


def test_plan_updated_translates_steps_and_status() -> None:
    """turn/plan/updated -> PLAN_UPDATED with step list + status (constructed).

    step text is the only field per TurnPlanStep (no id) — decision 2 makes it
    the upsert key. status is camelCase (turn.rs:441).
    """

    params = {
        "threadId": "t-1",
        "turnId": "turn-9",
        "explanation": "three steps",
        "plan": [
            {"step": "read translator", "status": "completed"},
            {"step": "split handlers", "status": "inProgress"},
            {"step": "add tests", "status": "pending"},
        ],
    }
    item = CodexTranslator()._on_plan_updated(params)[0]
    assert item.type is CodexEventType.PLAN_UPDATED
    assert item.thread_id == "t-1"
    assert item.turn_id == "turn-9"
    assert item.payload["explanation"] == "three steps"
    assert item.payload["steps"] == (
        {"step": "read translator", "status": "completed"},
        {"step": "split handlers", "status": "inProgress"},
        {"step": "add tests", "status": "pending"},
    )


def test_plan_updated_rejects_abandoned_status() -> None:
    """abandoned is not a protocol TurnPlanStepStatus — drift, not silent.

    Decision 1: no abandoned state. Interrupted plans surface via turn status,
    never via a step status value.
    """

    with pytest.raises(ProtocolViolationError):
        CodexTranslator()._on_plan_updated(
            {
                "threadId": "t",
                "turnId": "x",
                "plan": [{"step": "s", "status": "abandoned"}],
            }
        )


def test_subagent_item_translates_kind_and_agent_thread() -> None:
    """item.type=subAgentActivity -> SUBAGENT_ACTIVITY, no usage (decision 3)."""

    item = CodexTranslator()._subagent_item(
        {"threadId": "t-1", "turnId": "turn-1"},
        {
            "id": "sa-1",
            "kind": "started",
            "agentThreadId": "t-sub",
            "agentPath": "/agent/sub",
        },
    )
    assert item.type is CodexEventType.SUBAGENT_ACTIVITY
    assert item.item_id == "sa-1"
    assert item.payload["kind"] == "started"
    assert item.payload["agent_thread_id"] == "t-sub"
    assert item.payload["agent_path"] == "/agent/sub"
    # usage / tokens / summary are NOT protocol fields here — must not appear
    # fabricated (spec C-4: usage null 诚实).
    assert "usage" not in item.payload
    assert "tokens" not in item.payload
    assert "summary" not in item.payload


def test_compaction_item_carries_only_id() -> None:
    """item.type=contextCompaction -> COMPACTION, id only (decision 4).

    Pre/post token counts are read off thread/tokenUsage/updated (slice-071),
    never invented here.
    """

    item = CodexTranslator()._compaction_item(
        {"threadId": "t-1", "turnId": "turn-1"}, {"id": "comp-a3f"}
    )
    assert item.type is CodexEventType.COMPACTION
    assert item.item_id == "comp-a3f"
    assert dict(item.payload) == {}


def test_warning_translates_message_with_optional_thread() -> None:
    """warning / guardianWarning -> HOST_WARNING; thread_id optional.

    WarningNotification.thread_id is Option (notification.rs:21) — absent for
    global warnings. GuardianWarning carries thread_id (notification.rs:31).
    """

    guardian = CodexTranslator()._on_warning(
        {"threadId": "t-1", "message": "sandbox denial"}
    )[0]
    assert guardian.type is CodexEventType.HOST_WARNING
    assert guardian.thread_id == "t-1"
    assert guardian.payload["message"] == "sandbox denial"

    global_warn = CodexTranslator()._on_warning({"message": "global caution"})[0]
    assert global_warn.thread_id is None
    assert global_warn.payload["message"] == "global caution"


def test_warning_rejects_non_string_message() -> None:
    """warning.message must be a string — null / int must not become fake copy.

    Regression guard for the C-4 principle: ``_as_str(None)`` would otherwise
    render the literal "None" as warning text. ``_require`` only checks the key
    exists, so the handler adds the type check explicitly.
    """

    with pytest.raises(ProtocolViolationError):
        CodexTranslator()._on_warning({"threadId": "t-1", "message": None})
    with pytest.raises(ProtocolViolationError):
        CodexTranslator()._on_warning({"threadId": "t-1", "message": 123})


def test_slice077_skeleton_methods_are_capability_false() -> None:
    """plan/warning stay in _IGNORED_METHODS until activated (manager gates).

    The dispatch table still has them (handler ready), but the manager drops
    these methods before translate() fires — capability=false (slice-077).
    """

    translator = CodexTranslator()
    ignored = translator.ignored_methods
    for method in (
        "turn/plan/updated",
        "warning",
        "guardianWarning",
        "configWarning",
        "deprecationNotice",
    ):
        assert method in ignored, f"{method} should be capability=false"


def test_subagent_and_compaction_items_route_to_empty() -> None:
    """slice-088: item/completed(contextCompaction) now routes to a COMPACTION
    item (the context observer consumes it to advance generation). item/started
    is intentionally still empty — only completed closes a boundary (083 / A5).
    subAgentActivity remains capability=false until its own slice.
    """

    translator = CodexTranslator()
    assert (
        translator.translate(
            "item/started",
            {
                "threadId": "t",
                "turnId": "x",
                "item": {"type": "subAgentActivity", "id": "s"},
            },
        )
        == []
    )
    # A5: item/started(contextCompaction) is a no-op — thread/compact/start
    # returning {} is not a boundary; only item/completed closes one.
    assert (
        translator.translate(
            "item/started",
            {
                "threadId": "t",
                "turnId": "x",
                "item": {"type": "contextCompaction", "id": "c"},
            },
        )
        == []
    )
    completed = translator.translate(
        "item/completed",
        {
            "threadId": "t",
            "turnId": "x",
            "item": {"type": "contextCompaction", "id": "c"},
        },
    )
    assert len(completed) == 1
    assert completed[0].type.value == "compaction"
    assert completed[0].thread_id == "t"
    assert completed[0].turn_id == "x"
    assert completed[0].item_id == "c"
