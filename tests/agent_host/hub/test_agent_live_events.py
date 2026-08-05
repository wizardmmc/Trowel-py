"""验证两种 runtime 共用常驻 Agent 事件订阅。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trowel_py.agent_host.hub import SessionConflictError, SessionHub
from tests.agent_host.hub._support import cc_req


async def test_claude_subscriber_can_wait_before_lazy_process_start(
    hub: SessionHub, workdir: Path
) -> None:
    """新建 Claude 会话尚未拉起进程时也能先建立常驻订阅。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        host.running = True
        try:
            yield {"type": "text", "text": "first turn"}
            yield {"type": "finished", "duration_ms": 1}
        finally:
            host.running = False

    host.send = send
    live = hub.subscribe_agent_events(binding.session_id)
    first_event = asyncio.create_task(anext(live))
    await asyncio.sleep(0)
    assert not first_event.done()

    turn = asyncio.create_task(_collect(hub.stream(binding.session_id, "hello")))
    first = await asyncio.wait_for(first_event, timeout=0.2)
    await turn
    await live.aclose()

    assert first["type"] == "text"
    assert first["payload"]["text"] == "first turn"


async def test_claude_automatic_turn_reaches_live_subscriber(
    hub: SessionHub, workdir: Path
) -> None:
    """Agent Host 启动的 Claude 续轮在没有消息请求方时仍进入实时界面。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        host.running = True
        try:
            yield {
                "type": "turn_start",
                "turn_id": "cc-auto-turn",
                "revertible": False,
            }
            yield {"type": "text", "text": "automatic result"}
            yield {"type": "finished", "duration_ms": 1}
        finally:
            host.running = False

    host.send = send
    live = hub.subscribe_agent_events(binding.session_id)
    turn = asyncio.create_task(
        _collect(hub.run_automatic_turn(binding.session_id, "internal notice"))
    )

    first = await asyncio.wait_for(anext(live), timeout=0.2)
    second = await asyncio.wait_for(anext(live), timeout=0.2)
    third = await asyncio.wait_for(anext(live), timeout=0.2)
    await turn
    await live.aclose()

    assert first["type"] == "turn_start"
    assert first["payload"]["autonomous"] is True
    assert second["payload"]["text"] == "automatic result"
    assert third["type"] == "finished"


async def test_application_subscriber_multiplexes_user_sessions(
    hub: SessionHub, workdir: Path
) -> None:
    """一个应用订阅同时接收全部 user session，且不泄露 delegate 事件。"""

    first = hub.create(cc_req(workdir))
    second = hub.create(cc_req(workdir))
    delegate = hub.create(cc_req(workdir, session_kind="delegate"))
    live = hub.subscribe_application_events(queue_capacity=8)

    hub._publish_agent_event(
        first.session_id,
        _event(first.session_id, seq=1, text="first"),
    )
    hub._publish_agent_event(
        delegate.session_id,
        _event(delegate.session_id, seq=1, text="private"),
    )
    hub._publish_agent_event(
        second.session_id,
        _event(second.session_id, seq=1, text="second"),
    )

    first_delivery = await asyncio.wait_for(live.receive(), timeout=0.2)
    second_delivery = await asyncio.wait_for(live.receive(), timeout=0.2)
    live.close()

    assert first_delivery.event is not None
    assert second_delivery.event is not None
    assert [
        first_delivery.event["session_id"],
        second_delivery.event["session_id"],
    ] == [first.session_id, second.session_id]


async def test_application_subscriber_reports_only_the_dropped_session_gap(
    hub: SessionHub, workdir: Path
) -> None:
    """慢消费者溢出时只标记被丢事件所属的 session。"""

    first = hub.create(cc_req(workdir))
    second = hub.create(cc_req(workdir))
    live = hub.subscribe_application_events(queue_capacity=2)

    hub._publish_agent_event(first.session_id, _event(first.session_id, seq=1))
    hub._publish_agent_event(second.session_id, _event(second.session_id, seq=1))
    hub._publish_agent_event(second.session_id, _event(second.session_id, seq=2))

    gap = await asyncio.wait_for(live.receive(), timeout=0.2)
    next_event = await asyncio.wait_for(live.receive(), timeout=0.2)
    live.close()

    assert gap.gapped_session_id == first.session_id
    assert next_event.event is not None
    assert next_event.event["session_id"] == second.session_id


async def test_claude_turn_can_be_owned_by_application_stream(
    hub: SessionHub, workdir: Path
) -> None:
    """Claude 输入不再要求 renderer 持有第二条 POST SSE。"""

    binding = hub.create(cc_req(workdir))
    live = hub.subscribe_application_events(queue_capacity=8)

    turn_id = await hub.start_turn(binding.session_id, "hello")
    started = await asyncio.wait_for(live.receive(), timeout=0.2)
    delivery = await asyncio.wait_for(live.receive(), timeout=0.2)
    live.close()

    assert isinstance(turn_id, str)
    assert started.event is not None and started.event["turn_id"] == turn_id
    assert delivery.event is not None
    assert delivery.event["session_id"] == binding.session_id
    assert delivery.event["payload"]["text"] == "echo:hello"


async def test_sixth_user_turn_is_rejected_before_background_start(
    hub: SessionHub, workdir: Path
) -> None:
    """五个 Claude turn 已预留时，第六个 `/turns` 调用同步收到容量拒绝。"""

    release = asyncio.Event()
    bindings = [hub.create(cc_req(workdir)) for _ in range(6)]

    async def send(_text: str):
        """保持原生 turn 在跑，直到测试完成容量断言。"""

        yield {"type": "turn_start", "turn_id": "held-turn"}
        await release.wait()
        yield {"type": "finished", "duration_ms": 1}

    for binding in bindings:
        hub._cc_registry[binding.session_id].send = send

    for binding in bindings[:5]:
        assert isinstance(await hub.start_turn(binding.session_id, "hold"), str)

    with pytest.raises(SessionConflictError, match="同时 in-turn"):
        await hub.start_turn(bindings[5].session_id, "rejected")

    release.set()
    tasks = tuple(hub._detached_turn_tasks.values())
    await asyncio.gather(*tasks)


async def test_detached_local_command_gets_a_synthetic_root_terminal(
    hub: SessionHub, workdir: Path
) -> None:
    """原生流以本地命令结束时，后台 owner 补齐带同一 turn ID 的 terminal。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        """模拟不会自行发送 finished 的 Claude 本地命令。"""

        yield {"type": "turn_start", "turn_id": "cc-local-turn"}
        yield {"type": "local_command", "content": "cost: 0.1"}

    host.send = send
    live = hub.subscribe_application_events(queue_capacity=8)

    turn_id = await hub.start_turn(binding.session_id, "/cost")
    deliveries = [
        await asyncio.wait_for(live.receive(), timeout=0.2) for _ in range(3)
    ]
    live.close()
    events = [delivery.event for delivery in deliveries]

    assert [event["type"] for event in events if event is not None] == [
        "turn_start",
        "local_command",
        "finished",
    ]
    assert events[-1] is not None
    assert events[-1]["turn_id"] == turn_id


async def test_detached_stream_without_terminal_reports_a_root_error(
    hub: SessionHub, workdir: Path
) -> None:
    """普通原生流静默结束不能被伪装成成功。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        """模拟在 turn_start 后静默结束的损坏原生流。"""

        yield {"type": "turn_start", "turn_id": "cc-broken-turn"}

    host.send = send
    live = hub.subscribe_application_events(queue_capacity=8)

    turn_id = await hub.start_turn(binding.session_id, "hello")
    accepted = await asyncio.wait_for(live.receive(), timeout=0.2)
    failed = await asyncio.wait_for(live.receive(), timeout=0.2)
    live.close()

    assert accepted.event is not None and accepted.event["turn_id"] == turn_id
    assert failed.event is not None and failed.event["type"] == "error"
    assert failed.event["turn_id"] == turn_id
    assert "without a terminal event" in failed.event["payload"]["errors"][0]


async def test_detached_failure_before_first_native_event_keeps_accepted_turn_id(
    hub: SessionHub, workdir: Path
) -> None:
    """CC 首帧前失败仍以接收响应中的根 turn ID 发布终态。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        """模拟 checkpoint 或进程启动在首个 yield 前失败。"""

        if False:  # pragma: no cover - 保持此测试替身为 async generator。
            yield {}
        raise RuntimeError("failure before first native event")

    host.send = send
    live = hub.subscribe_application_events(queue_capacity=8)

    turn_id = await hub.start_turn(binding.session_id, "hello")
    started = await asyncio.wait_for(live.receive(), timeout=0.2)
    failed = await asyncio.wait_for(live.receive(), timeout=0.2)
    live.close()

    assert isinstance(turn_id, str)
    assert started.event is not None and started.event["turn_id"] == turn_id
    assert failed.event is not None and failed.event["type"] == "error"
    assert failed.event["turn_id"] == turn_id


async def test_detached_session_exit_does_not_add_a_late_error(
    hub: SessionHub, workdir: Path
) -> None:
    """真实 `/exit` 的 session_exited 是 owner 合法终点，不再补失败事件。"""

    binding = hub.create(cc_req(workdir))
    host = hub._cc_registry[binding.session_id]

    async def send(_text: str):
        """模拟 Claude Code 退出控制通道。"""

        yield {"type": "session_exited", "cc_session_id": "native-exited"}

    host.send = send
    live = hub.subscribe_application_events(queue_capacity=8)

    await hub.start_turn(binding.session_id, "/exit")
    started = await asyncio.wait_for(live.receive(), timeout=0.2)
    exited = await asyncio.wait_for(live.receive(), timeout=0.2)
    await asyncio.gather(*tuple(hub._detached_turn_tasks.values()))

    assert started.event is not None and started.event["type"] == "turn_start"
    assert exited.event is not None and exited.event["type"] == "session_exited"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(live.receive(), timeout=0.01)
    live.close()


def test_active_snapshot_exposes_orthogonal_lifecycle_state(
    hub: SessionHub, workdir: Path
) -> None:
    """active snapshot 明确返回资源、根 turn、实时流和状态代次。"""

    binding = hub.create(cc_req(workdir))
    sessions, _ = hub.list_active()
    snapshot = next(item for item in sessions if item["session_id"] == binding.session_id)

    assert snapshot["resource_state"] == "connected"
    assert snapshot["turn_state"] == "idle"
    assert snapshot["current_turn_id"] is None
    assert snapshot["state_generation"] >= 1


def test_active_snapshot_ignores_an_old_root_terminal(
    hub: SessionHub, workdir: Path
) -> None:
    """旧 turn 的晚到终态不能覆盖 snapshot 中正在运行的新 turn。"""

    binding = hub.create(cc_req(workdir))
    hub._cc_registry[binding.session_id].running = True
    hub._publish_agent_event(
        binding.session_id,
        {
            **_event(binding.session_id, seq=1),
            "type": "turn_start",
            "turn_id": "turn-new",
        },
    )
    hub._publish_agent_event(
        binding.session_id,
        {
            **_event(binding.session_id, seq=2),
            "type": "finished",
            "turn_id": "turn-old",
        },
    )

    sessions, _ = hub.list_active()
    snapshot = next(item for item in sessions if item["session_id"] == binding.session_id)

    assert snapshot["turn_state"] == "running"
    assert snapshot["current_turn_id"] == "turn-new"


def test_active_snapshot_keeps_root_terminal_during_runtime_cleanup(
    hub: SessionHub, workdir: Path
) -> None:
    """终态已发布但 runtime 尚在清理时，snapshot 不得反写为 running。"""

    binding = hub.create(cc_req(workdir))
    hub._cc_registry[binding.session_id].running = True
    hub._publish_agent_event(
        binding.session_id,
        {
            **_event(binding.session_id, seq=1),
            "type": "turn_start",
            "turn_id": "turn-cleaning-up",
        },
    )
    hub._publish_agent_event(
        binding.session_id,
        {
            **_event(binding.session_id, seq=2),
            "type": "finished",
            "turn_id": "turn-cleaning-up",
        },
    )

    sessions, _ = hub.list_active()
    snapshot = next(item for item in sessions if item["session_id"] == binding.session_id)

    assert snapshot["running"] is True
    assert snapshot["turn_state"] == "completed"


def test_active_snapshot_does_not_report_a_stale_running_turn(
    hub: SessionHub, workdir: Path
) -> None:
    """runtime 已无在途 turn 时，缺失 terminal 的旧状态不能继续宣称运行中。"""

    binding = hub.create(cc_req(workdir))
    hub._publish_agent_event(
        binding.session_id,
        {
            **_event(binding.session_id, seq=1),
            "type": "turn_start",
            "turn_id": "turn-without-terminal",
        },
    )

    sessions, _ = hub.list_active()
    snapshot = next(item for item in sessions if item["session_id"] == binding.session_id)

    assert snapshot["running"] is False
    assert snapshot["turn_state"] == "failed"


def _event(session_id: str, *, seq: int, text: str = "event") -> dict:
    """创建测试使用的最小 AgentEvent wire 字典。"""

    return {
        "schema": "agent-event-v1",
        "session_id": session_id,
        "runtime": "claude_code",
        "seq": seq,
        "type": "text",
        "thread_id": None,
        "turn_id": None,
        "item_id": None,
        "payload": {"text": text},
    }


async def _collect(stream):
    """把内部续轮事件消费到终态。"""

    return [event async for event in stream]
