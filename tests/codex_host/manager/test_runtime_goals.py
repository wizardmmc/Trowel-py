from __future__ import annotations

from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import (
    _cfg,
    _init_resp,
    _manager,
    _thread_result,
)
from trowel_py.codex_host import CodexSession


async def test_goal_get_set_and_clear_use_attached_thread() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        assert msg["method"] == "thread/start"
        yield Step.send({"id": msg["id"], "result": _thread_result("thread-1")})

        msg = yield Step.recv()
        assert msg == {
            "method": "thread/goal/get",
            "id": msg["id"],
            "params": {"threadId": "thread-1"},
        }
        yield Step.send({"id": msg["id"], "result": {"goal": None}})

        msg = yield Step.recv()
        assert msg["method"] == "thread/goal/set"
        assert msg["params"] == {
            "threadId": "thread-1",
            "objective": "Ship the rail",
            "status": "active",
            "tokenBudget": 12000,
        }
        goal = {
            "threadId": "thread-1",
            "objective": "Ship the rail",
            "status": "active",
            "tokenBudget": 12000,
            "tokensUsed": 0,
            "timeUsedSeconds": 0,
            "createdAt": 10,
            "updatedAt": 10,
        }
        yield Step.send({"id": msg["id"], "result": {"goal": goal}})

        msg = yield Step.recv()
        assert msg["method"] == "thread/goal/clear"
        assert msg["params"] == {"threadId": "thread-1"}
        yield Step.send({"id": msg["id"], "result": {"cleared": True}})
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    assert await manager.get_goal(session) is None
    goal = await manager.set_goal(
        session,
        objective="Ship the rail",
        status="active",
        token_budget=12000,
        token_budget_supplied=True,
    )
    assert goal["objective"] == "Ship the rail"
    assert await manager.clear_goal(session) is True
    await manager.close()
