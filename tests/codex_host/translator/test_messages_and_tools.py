from __future__ import annotations

import pytest

from trowel_py.codex_host.events import (
    CodexEventType,
)
from trowel_py.codex_host.translator import CodexTranslator
from tests.codex_host.translator._support import (
    _by_method,
    _command_action_notifications,
    _notifications,
)


def test_agent_message_delta_translates_to_assistant_delta() -> None:

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

    msg = _by_method("item/started")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_STARTED
    native_item = msg["params"]["item"]
    assert item.item_id == native_item["id"]
    assert item.payload["command"] == native_item["command"]
    assert item.payload["cwd"] == native_item["cwd"]
    assert item.payload["started_at"] == msg["params"]["startedAtMs"]


def test_command_completed_translates_to_tool_completed() -> None:

    msg = next(
        m
        for m in _notifications()
        if m["method"] == "item/completed"
        and m["params"]["item"]["type"] == "commandExecution"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.TOOL_COMPLETED
    native_item = msg["params"]["item"]
    assert item.payload["exit_code"] == native_item["exitCode"]
    assert item.payload["output"] == native_item["aggregatedOutput"]
    assert item.payload["duration_ms"] == native_item["durationMs"]
    assert item.payload["status"] == "completed"


def test_mcp_tool_started_translates_to_tool_started() -> None:

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

    # v2/item.rs 要求 mcpToolCall 提供 id、server、tool 和 status。
    from trowel_py.codex_host.errors import ProtocolViolationError

    params = {
        "threadId": "thr-1",
        "turnId": "trn-1",
        "item": {"id": "call-3", "type": "mcpToolCall"},
    }
    with pytest.raises(ProtocolViolationError):
        CodexTranslator().translate("item/started", params)


def test_mcp_tool_provenance_fields_pass_through() -> None:

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

    msg = next(
        m
        for m in _notifications()
        if m["method"] == "item/completed"
        and m["params"]["item"]["type"] == "agentMessage"
    )
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.ASSISTANT_MESSAGE
    native_item = msg["params"]["item"]
    assert item.payload["text"] == native_item["text"]
    assert item.payload["phase"] == native_item["phase"]


def test_token_usage_translates_to_usage_updated() -> None:

    msg = _by_method("thread/tokenUsage/updated")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.USAGE_UPDATED
    usage = msg["params"]["tokenUsage"]
    assert item.payload["total"] == usage["total"]
    assert item.payload["last"] == usage["last"]
    assert item.payload["model_context_window"] == usage["modelContextWindow"]
