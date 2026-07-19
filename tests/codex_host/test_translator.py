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
