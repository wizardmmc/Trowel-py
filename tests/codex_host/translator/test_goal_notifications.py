from __future__ import annotations

from trowel_py.codex_host.events import CodexEventType
from trowel_py.codex_host.translator import CodexTranslator
from tests.codex_host.translator._support import _by_method


def test_recorded_goal_updated_keeps_full_snapshot() -> None:
    msg = _by_method("thread/goal/updated")

    item = CodexTranslator().translate(msg["method"], msg["params"])[0]

    assert item.type is CodexEventType.GOAL_UPDATED
    assert item.thread_id == "<uuid>"
    assert item.turn_id is None
    assert item.payload == {
        "objective": "Verify the native Goal and Plan event flow",
        "status": "active",
        "token_budget": 12000,
        "tokens_used": 0,
        "time_used_seconds": 0,
        "created_at": 1785039580,
        "updated_at": 1785039580,
    }


def test_recorded_goal_cleared_emits_explicit_clear() -> None:
    msg = _by_method("thread/goal/cleared")

    item = CodexTranslator().translate(msg["method"], msg["params"])[0]

    assert item.type is CodexEventType.GOAL_CLEARED
    assert item.thread_id == "<uuid>"
    assert dict(item.payload) == {}
