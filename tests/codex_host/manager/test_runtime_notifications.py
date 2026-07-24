from __future__ import annotations

import asyncio


from trowel_py.codex_host import (
    AppServerClient,
    CodexEventType,
    CodexHostManager,
    CodexHostManagerState,
    CodexSession,
)
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import (
    _behavior_server,
    _cfg,
    _manager,
)


async def test_stale_eof_does_not_degrade_new_connection() -> None:

    class SlowStartingClient:
        def __init__(self) -> None:

            self.start_entered = asyncio.Event()
            self.release_start = asyncio.Event()
            self.closed_event = asyncio.Event()
            self.closed = False

        async def start(self) -> None:

            self.start_entered.set()
            await self.release_start.wait()

        def add_notification_listener(self, listener) -> None:

            del listener

        def register_server_request_handler(self, method, handler) -> None:

            del method, handler

        def register_unknown_server_request_handler(self, handler) -> None:

            del handler

        async def wait_closed(self) -> None:

            await self.closed_event.wait()

        async def close(self) -> None:

            self.closed = True
            self.closed_event.set()

    current = SlowStartingClient()
    manager = CodexHostManager(client_factory=lambda: current)  # type: ignore[arg-type]
    stale = AppServerClient(expected_version=None)
    manager._client = stale  # noqa: SLF001
    manager._state = CodexHostManagerState.DEGRADED  # noqa: SLF001
    manager._attached_session_ids.add("s1")  # noqa: SLF001

    ready_task = asyncio.create_task(manager.ensure_ready())
    await current.start_entered.wait()
    await manager._on_unexpected_exit(stale)  # noqa: SLF001

    assert manager.client is current
    assert manager.state is CodexHostManagerState.STARTING
    current.release_start.set()
    assert await ready_task is current
    await manager.close()


async def test_orphan_unknown_thread_is_not_routed() -> None:

    ghost_note = {
        "method": "item/agentMessage/delta",
        "params": {"threadId": "t-ghost", "turnId": "x", "itemId": "m", "delta": "boo"},
    }
    fake = FakeAppServer(_behavior_server(orphan_before=[ghost_note]))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.send(session, "hi")
    await asyncio.sleep(0.05)
    events = session.drain()

    assert not any("boo" in str(e.payload) for e in events)

    orphans = manager.orphans
    assert any(
        o.method == "item/agentMessage/delta" and o.thread_id == "t-ghost"
        for o in orphans
    )
    await manager.close()


async def test_orphan_unknown_method_is_recorded() -> None:

    unknown_note = {
        "method": "some/future/thing",
        "params": {"threadId": None},
    }
    fake = FakeAppServer(_behavior_server(orphan_before=[unknown_note]))
    manager = _manager(fake)
    await manager.ensure_ready()
    await asyncio.sleep(0.05)

    orphans = manager.orphans
    assert any(o.method == "some/future/thing" for o in orphans)
    await manager.close()


async def test_rate_limit_update_fans_out_to_bound_session() -> None:

    rate_limit_note = {
        "method": "account/rateLimits/updated",
        "params": {
            "rateLimits": {
                "limitId": "codex",
                "limitName": None,
                "primary": {
                    "usedPercent": 84,
                    "windowDurationMins": 10080,
                    "resetsAt": 1784949908,
                },
                "secondary": None,
                "credits": {"hasCredits": True, "unlimited": False, "balance": "120"},
                "individualLimit": None,
                "planType": "pro",
                "rateLimitReachedType": None,
            }
        },
    }

    def inject(thread_id, turn_id):
        return [Step.send(rate_limit_note)]

    fake = FakeAppServer(_behavior_server(on_turn=inject))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.send(session, "hi")
    await asyncio.sleep(0.05)
    events = session.drain()

    assert any(e.type is CodexEventType.RATE_LIMIT_UPDATED for e in events)

    assert not any(o.method == "account/rateLimits/updated" for o in manager.orphans)
    await manager.close()


async def test_rate_limit_update_fans_out_to_every_active_session() -> None:

    rate_limit_note = {
        "method": "account/rateLimits/updated",
        "params": {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 84, "resetsAt": 1784949908},
                "planType": "pro",
                "rateLimitReachedType": None,
            }
        },
    }

    def inject(thread_id, turn_id):
        return [Step.send(rate_limit_note)]

    fake = FakeAppServer(_behavior_server(on_turn=inject))
    manager = _manager(fake)
    session_a = CodexSession(_cfg("s-a"))
    session_b = CodexSession(_cfg("s-b"))
    manager.register(session_a)
    manager.register(session_b)

    await manager.send(session_a, "hi")
    await asyncio.sleep(0.05)
    events_a = session_a.drain()
    events_b = session_b.drain()
    assert any(e.type is CodexEventType.RATE_LIMIT_UPDATED for e in events_a)
    assert any(e.type is CodexEventType.RATE_LIMIT_UPDATED for e in events_b)
    await manager.close()
