"""验证 discussion attempt 从 Claude Code 原生历史精确切轮。"""

import hashlib

from trowel_py.agent_host.history_turn import select_cc_turn_by_input_hash
from trowel_py.cc_host.schemas import TextEvent, ThinkingEvent, UserEvent


def test_selects_exact_user_boundary_without_borrowing_next_turn() -> None:
    """目标输入后的事件保留，到下一条 user 为止。"""

    target = "第二轮共同输入"
    events = [
        UserEvent(text="第一轮"),
        TextEvent(text="第一轮回答"),
        UserEvent(text=target),
        ThinkingEvent(text="检查", thinking_duration_seconds=1),
        TextEvent(text="第二轮回答"),
        UserEvent(text="第三轮"),
        TextEvent(text="第三轮回答"),
    ]

    selected = select_cc_turn_by_input_hash(
        events,
        hashlib.sha256(target.encode()).hexdigest(),
    )

    assert [event.type for event in selected] == ["user", "thinking", "text"]


def test_ambiguous_input_hash_fails_closed() -> None:
    """相同输入出现两次时不能猜测是哪一轮。"""

    target = "重复输入"
    selected = select_cc_turn_by_input_hash(
        [UserEvent(text=target), UserEvent(text=target)],
        hashlib.sha256(target.encode()).hexdigest(),
    )

    assert selected == []


def test_selects_persisted_occurrence_when_same_input_was_retried() -> None:
    """重复 prompt 用 durable 接受次序定位，不能恢复成较早一次。"""

    target = "重复输入"
    events = [
        UserEvent(text=target),
        TextEvent(text="第一次失败前的文字"),
        UserEvent(text=target),
        ThinkingEvent(text="第二次核验", thinking_duration_seconds=1),
        TextEvent(text="第二次最终回答"),
    ]

    selected = select_cc_turn_by_input_hash(
        events,
        hashlib.sha256(target.encode()).hexdigest(),
        occurrence=2,
    )

    assert [event.type for event in selected] == ["user", "thinking", "text"]
    assert selected[-1].text == "第二次最终回答"
