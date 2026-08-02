from __future__ import annotations

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEventType,
)
from trowel_py.codex_host.translator import CodexTranslator
from tests.codex_host.translator._support import _by_method

# Plan shape 已由 Codex 0.144.0 真实录制固定；dispatch 仍保持 capability=false，
# 等待共享事件词表和前端 consumer 一起接通。


def test_plan_updated_translates_steps_and_status() -> None:

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


def test_recorded_plan_notification_is_enabled() -> None:
    msg = _by_method("turn/plan/updated")
    translator = CodexTranslator()

    assert msg["method"] not in translator.ignored_methods
    item = translator.translate(msg["method"], msg["params"])[0]
    assert item.type is CodexEventType.PLAN_UPDATED
    assert [step["status"] for step in item.payload["steps"]] == [
        "completed",
        "inProgress",
        "pending",
    ]


def test_plan_updated_rejects_abandoned_status() -> None:

    with pytest.raises(ProtocolViolationError):
        CodexTranslator()._on_plan_updated(
            {
                "threadId": "t",
                "turnId": "x",
                "plan": [{"step": "s", "status": "abandoned"}],
            }
        )


def test_subagent_item_translates_kind_and_agent_thread() -> None:

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

    # usage、tokens 和 summary 不是该协议类型的字段，不能自行补造。
    assert "usage" not in item.payload
    assert "tokens" not in item.payload
    assert "summary" not in item.payload


def test_compaction_item_carries_only_id() -> None:

    item = CodexTranslator()._compaction_item(
        {"threadId": "t-1", "turnId": "turn-1"}, {"id": "comp-a3f"}
    )
    assert item.type is CodexEventType.COMPACTION
    assert item.item_id == "comp-a3f"
    assert dict(item.payload) == {}


@pytest.mark.parametrize(
    ("item_type", "phase"),
    [("enteredReviewMode", "entered"), ("exitedReviewMode", "exited")],
)
def test_review_mode_completed_item_keeps_native_review(
    item_type: str, phase: str
) -> None:
    # item 字段来自 0.144.0 生成的 ThreadItem schema。
    items = CodexTranslator().translate(
        "item/completed",
        {
            "threadId": "t-1",
            "turnId": "turn-1",
            "item": {"type": item_type, "id": "review-1", "review": "Review changes"},
        },
    )

    assert len(items) == 1
    assert items[0].type is CodexEventType.REVIEW_MODE
    assert items[0].payload == {"phase": phase, "review": "Review changes"}


def test_warning_translates_message_with_optional_thread() -> None:

    # notification.rs 将 warning.threadId 定义为 Optional，全局告警允许缺失。
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

    with pytest.raises(ProtocolViolationError):
        CodexTranslator()._on_warning({"threadId": "t-1", "message": None})
    with pytest.raises(ProtocolViolationError):
        CodexTranslator()._on_warning({"threadId": "t-1", "message": 123})


def test_untranslated_skeleton_methods_remain_capability_false() -> None:

    translator = CodexTranslator()
    ignored = translator.ignored_methods
    for method in (
        "warning",
        "guardianWarning",
        "configWarning",
        "deprecationNotice",
    ):
        assert method in ignored, f"{method} should be capability=false"


def test_malformed_subagent_is_rejected_and_compaction_has_started_status() -> None:

    translator = CodexTranslator()
    with pytest.raises(ProtocolViolationError, match="agentThreadId"):
        translator.translate(
            "item/started",
            {
                "threadId": "t",
                "turnId": "x",
                "item": {"type": "subAgentActivity", "id": "s"},
            },
        )

    started = translator.translate(
        "item/started",
        {
            "threadId": "t",
            "turnId": "x",
            "item": {"type": "contextCompaction", "id": "c"},
        },
    )
    assert len(started) == 1
    assert started[0].type.value == "status"
    assert started[0].thread_id == "t"
    assert started[0].turn_id == "x"
    assert started[0].item_id == "c"
    assert started[0].payload == {"status": "compacting", "active_flags": ()}

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
