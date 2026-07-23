from __future__ import annotations

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEventType,
    HostStatusKind,
    host_status_item,
)
from trowel_py.codex_host.translator import CodexTranslator
from tests.codex_host.translator._support import (
    _by_method,
    _notifications,
)


def test_turn_completed_status_completed_translates_to_finished() -> None:

    msg = _by_method("turn/completed")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.FINISHED
    assert item.turn_id == msg["params"]["turn"]["id"]
    assert item.payload["status"] == "completed"
    assert item.payload["duration_ms"] == msg["params"]["turn"]["durationMs"]


def test_thread_status_changed_translates_to_status() -> None:

    msg = _by_method("thread/status/changed")
    item = CodexTranslator().translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.STATUS
    assert item.payload["status"] == msg["params"]["status"]["type"]
    assert item.payload["active_flags"] == tuple(msg["params"]["status"]["activeFlags"])


def test_turn_completed_interrupted_translates_to_interrupted() -> None:

    # 以下 shape 来自 Codex 0.144.0 的 v2/turn.rs、v2/item.rs 与 v2/notification.rs。
    params = {
        "threadId": "t-1",
        "turn": {"id": "turn-9", "status": "interrupted", "durationMs": 120},
    }
    item = CodexTranslator().translate("turn/completed", params)[0]
    assert item.type is CodexEventType.INTERRUPTED
    assert item.turn_id == "turn-9"
    assert item.payload["status"] == "interrupted"


def test_turn_completed_failed_translates_to_error() -> None:

    params = {
        "threadId": "t-1",
        "turn": {"id": "turn-9", "status": "failed", "error": {"message": "boom"}},
    }
    item = CodexTranslator().translate("turn/completed", params)[0]
    assert item.type is CodexEventType.ERROR
    assert item.payload["status"] == "failed"
    assert item.payload["error"] == {"message": "boom"}


def test_reasoning_text_delta_translates_to_reasoning_delta() -> None:

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


def test_unknown_method_returns_empty() -> None:

    assert CodexTranslator().translate("some/future/method", {"threadId": "t"}) == []


def test_ignored_methods_return_empty() -> None:

    translator = CodexTranslator()
    assert translator.translate("thread/started", {"thread": {"id": "t"}}) == []
    assert (
        translator.translate("turn/started", {"threadId": "t", "turn": {"id": "x"}})
        == []
    )
    assert translator.translate("mcpServer/startupStatus/updated", {}) == []


def test_missing_required_field_raises_protocol_violation() -> None:

    translator = CodexTranslator()
    with pytest.raises(ProtocolViolationError):
        translator.translate("item/agentMessage/delta", {"threadId": "t"})


def test_unexpected_turn_status_raises_protocol_violation() -> None:

    params = {
        "threadId": "t-1",
        "turn": {"id": "turn-9", "status": "inProgress"},
    }
    with pytest.raises(ProtocolViolationError):
        CodexTranslator().translate("turn/completed", params)


def test_host_status_item_carries_status_and_reason() -> None:

    item = host_status_item(
        HostStatusKind.HOST_EXITED, thread_id="t-1", reason="eof", exit_code=1
    )
    assert item.type is CodexEventType.HOST_STATUS
    assert item.thread_id == "t-1"
    assert item.payload["status"] == "host_exited"
    assert item.payload["reason"] == "eof"
    assert item.payload["exit_code"] == 1


def test_full_fixture_replay_produces_no_protocol_errors() -> None:

    translator = CodexTranslator()
    emitted = 0
    for msg in _notifications():
        emitted += len(translator.translate(msg["method"], msg["params"]))

    assert emitted >= 8
