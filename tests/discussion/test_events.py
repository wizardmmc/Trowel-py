"""验证 discussion SSE 唤醒总线跨线程时仍只操作 owner event loop。"""

from __future__ import annotations

import asyncio

import pytest

from trowel_py.discussion.events import DiscussionEventBus


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
