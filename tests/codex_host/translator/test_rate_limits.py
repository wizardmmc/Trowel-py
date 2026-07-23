from __future__ import annotations

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEventType,
)
from trowel_py.codex_host.translator import CodexTranslator
from tests.codex_host.translator._support import (
    _by_method,
)


def test_rate_limits_updated_translates_to_rate_limit_updated() -> None:

    # 真实录制对应 account.rs:518 的 account-level 通知，因此没有 threadId。
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

    assert item.payload["primary"] == snapshot["primary"]
    assert item.payload["secondary"] == snapshot["secondary"]
    assert item.payload["credits"] == snapshot["credits"]
    assert item.payload["individual_limit"] == snapshot["individualLimit"]

    # account.rs:534 将 spendControlReached 定义为 Optional，缺失时不得伪造。
    assert item.payload["spend_control_reached"] is None


def test_rate_limits_missing_field_raises_protocol_violation() -> None:

    with pytest.raises(ProtocolViolationError):
        CodexTranslator().translate("account/rateLimits/updated", {})
