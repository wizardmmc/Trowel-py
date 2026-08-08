"""验证 discussion SSE 唤醒总线跨线程时仍只操作 owner event loop。"""

from __future__ import annotations

import asyncio

import pytest

from trowel_py.discussion.events import AttemptLiveEventPublisher, DiscussionEventBus


@pytest.mark.anyio
async def test_publish_from_worker_thread_wakes_owner_loop_safely() -> None:
    """同步 service 即使在线程池提交，也不能跨线程直接操作 asyncio.Queue。"""

    bus = DiscussionEventBus()
    subscription = bus.subscribe("discussion-thread-safe")
    try:
        await asyncio.to_thread(bus.publish, "discussion-thread-safe")
        assert await subscription.wait(timeout=1) is True
    finally:
        subscription.close()


@pytest.mark.anyio
async def test_attempt_overflow_emits_gap_without_losing_state_wakeup() -> None:
    """满队列必须同时保留补历史身份和独立的 SQLite 重查信号。"""

    bus = DiscussionEventBus(attempt_capacity=1)
    subscription = bus.subscribe("discussion-gap")
    try:
        bus.publish_attempt_event(
            discussion_id="discussion-gap",
            round_number=1,
            participant_id="participant-a",
            attempt_id="attempt-a",
            attempt_sequence=1,
            event={"type": "thinking", "seq": 1},
        )
        bus.publish_attempt_event(
            discussion_id="discussion-gap",
            round_number=1,
            participant_id="participant-a",
            attempt_id="attempt-a",
            attempt_sequence=2,
            event={"type": "text", "seq": 2},
        )
        bus.publish("discussion-gap")

        gap = await subscription.receive(timeout=1)
        live = await subscription.receive(timeout=1)
        state = await subscription.receive(timeout=1)
    finally:
        subscription.close()

    assert gap is not None and gap.kind == "attempt_gap"
    assert gap.payload == {
        "type": "attempt_gap",
        "discussion_id": "discussion-gap",
        "round_number": 1,
        "participant_id": "participant-a",
        "attempt_id": "attempt-a",
    }
    assert live is not None and live.kind == "attempt_event"
    assert live.payload is not None and live.payload["attempt_sequence"] == 2
    assert state is not None and state.kind == "state_changed"


@pytest.mark.anyio
async def test_attempt_publisher_coalesces_deltas_and_keeps_thinking_start_signal(
) -> None:
    """正文分片只占一个槽；思考首帧立即可见，终态前保留最新进度。"""

    bus = DiscussionEventBus(attempt_capacity=4)
    subscription = bus.subscribe("discussion-coalesce")
    publisher = AttemptLiveEventPublisher(
        bus,
        discussion_id="discussion-coalesce",
        round_number=1,
        participant_id="participant-a",
        attempt_id="attempt-a",
        coalesce_seconds=1,
    )
    common = {
        "schema": "agent-event-v1",
        "session_id": "session-a",
        "runtime": "claude_code",
        "turn_id": "turn-a",
        "item_id": None,
    }
    try:
        publisher.publish(
            {**common, "seq": 1, "type": "text", "payload": {"text": "前半"}}
        )
        publisher.publish(
            {**common, "seq": 2, "type": "text", "payload": {"text": "后半"}}
        )
        publisher.publish(
            {
                **common,
                "seq": 3,
                "type": "thinking_progress",
                "payload": {"estimated_tokens": 99},
            }
        )
        publisher.publish(
            {
                **common,
                "seq": 4,
                "type": "thinking_progress",
                "payload": {"estimated_tokens": 120},
            }
        )
        publisher.publish(
            {**common, "seq": 5, "type": "finished", "payload": {}}
        )

        text_delivery = await subscription.receive(timeout=1)
        thinking_start_delivery = await subscription.receive(timeout=1)
        thinking_latest_delivery = await subscription.receive(timeout=1)
        terminal_delivery = await subscription.receive(timeout=1)
    finally:
        publisher.close()
        subscription.close()

    assert text_delivery is not None and text_delivery.payload is not None
    assert text_delivery.payload["attempt_sequence"] == 1
    assert text_delivery.payload["event"]["seq"] == 2
    assert text_delivery.payload["event"]["payload"]["text"] == "前半后半"
    assert (
        thinking_start_delivery is not None
        and thinking_start_delivery.payload is not None
    )
    assert thinking_start_delivery.payload["attempt_sequence"] == 2
    assert thinking_start_delivery.payload["event"]["type"] == "thinking_progress"
    assert (
        thinking_start_delivery.payload["event"]["payload"]["estimated_tokens"]
        == 99
    )
    assert (
        thinking_latest_delivery is not None
        and thinking_latest_delivery.payload is not None
    )
    assert thinking_latest_delivery.payload["attempt_sequence"] == 3
    assert thinking_latest_delivery.payload["event"]["type"] == "thinking_progress"
    assert (
        thinking_latest_delivery.payload["event"]["payload"]["estimated_tokens"]
        == 120
    )
    assert terminal_delivery is not None and terminal_delivery.payload is not None
    assert terminal_delivery.payload["attempt_sequence"] == 4
    assert terminal_delivery.payload["event"]["type"] == "finished"


@pytest.mark.anyio
async def test_two_claude_publishers_keep_interleaved_progress_isolated() -> None:
    """两个 Claude attempt 交错更新时，各自序号和最新 token 不能串卡。"""

    bus = DiscussionEventBus(attempt_capacity=8)
    subscription = bus.subscribe("discussion-two-claude")
    publishers = [
        AttemptLiveEventPublisher(
            bus,
            discussion_id="discussion-two-claude",
            round_number=1,
            participant_id=f"participant-{name}",
            attempt_id=f"attempt-{name}",
            coalesce_seconds=1,
        )
        for name in ("a", "b")
    ]

    def progress(session: str, seq: int, tokens: int) -> dict[str, object]:
        """构造与 Claude Code adapter 一致的累计思考进度。"""

        return {
            "schema": "agent-event-v1",
            "session_id": session,
            "runtime": "claude_code",
            "seq": seq,
            "type": "thinking_progress",
            "turn_id": f"turn-{session}",
            "item_id": None,
            "payload": {"estimated_tokens": tokens},
        }

    try:
        publishers[0].publish(progress("session-a", 1, 1))
        publishers[1].publish(progress("session-b", 1, 2))
        publishers[0].publish(progress("session-a", 2, 101))
        publishers[1].publish(progress("session-b", 2, 202))
        publishers[0].close()
        publishers[1].close()
        deliveries = [await subscription.receive(timeout=1) for _ in range(4)]
    finally:
        for publisher in publishers:
            publisher.close()
        subscription.close()

    per_attempt = {
        attempt: [
            delivery.payload
            for delivery in deliveries
            if delivery is not None
            and delivery.payload is not None
            and delivery.payload["attempt_id"] == attempt
        ]
        for attempt in ("attempt-a", "attempt-b")
    }
    assert [item["attempt_sequence"] for item in per_attempt["attempt-a"]] == [1, 2]
    assert [item["attempt_sequence"] for item in per_attempt["attempt-b"]] == [1, 2]
    assert [
        item["event"]["payload"]["estimated_tokens"]
        for item in per_attempt["attempt-a"]
    ] == [1, 101]
    assert [
        item["event"]["payload"]["estimated_tokens"]
        for item in per_attempt["attempt-b"]
    ] == [2, 202]


@pytest.mark.anyio
async def test_attempt_publisher_flushes_text_while_turn_is_still_running() -> None:
    """没有后续工具或终态时，定时器也必须把当前文字及时送到观察者。"""

    bus = DiscussionEventBus()
    subscription = bus.subscribe("discussion-timed-flush")
    publisher = AttemptLiveEventPublisher(
        bus,
        discussion_id="discussion-timed-flush",
        round_number=1,
        participant_id="participant-a",
        attempt_id="attempt-a",
        coalesce_seconds=0,
    )
    try:
        publisher.publish(
            {
                "schema": "agent-event-v1",
                "session_id": "session-a",
                "runtime": "codex",
                "seq": 1,
                "type": "text",
                "turn_id": "turn-a",
                "thread_id": "thread-a",
                "item_id": "message-a",
                "payload": {"text": "仍在生成中的正文"},
            }
        )
        delivery = await subscription.receive(timeout=1)
    finally:
        publisher.close()
        subscription.close()

    assert delivery is not None and delivery.payload is not None
    assert delivery.payload["event"]["payload"]["text"] == "仍在生成中的正文"
