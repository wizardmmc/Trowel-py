from __future__ import annotations

import asyncio

import pytest

from trowel_py.codex_host import CodexEventType, CodexSession, CodexSessionState
from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.session import TurnConflictError
from trowel_py.codex_host.skills import parse_skill_catalog
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import (
    _behavior_server,
    _cfg,
    _init_resp,
    _manager,
    _thread_result,
)


async def test_manager_lists_commands_for_connected_validated_version() -> None:
    fake = FakeAppServer(_behavior_server())
    manager = _manager(fake)

    roster = await manager.list_commands()

    assert [command["name"] for command in roster] == [
        "status",
        "compact",
        "review",
        "goal",
        "diff",
        "agent",
    ]
    await manager.close()


async def test_manager_lists_and_redacts_session_scoped_skills() -> None:
    """技能目录只返回输入框所需字段，不泄露技能文件的绝对路径。"""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        assert msg["method"] == "skills/list"
        assert msg["params"] == {"cwds": ["/tmp/x"], "forceReload": False}
        yield Step.send(
            {
                "id": msg["id"],
                "result": {
                    "data": [
                        {
                            "cwd": "/tmp/x",
                            "skills": [
                                {
                                    "name": "development-slice-workflow",
                                    "description": "推进开发 slice",
                                    "path": "/private/codex/skills/workflow/SKILL.md",
                                    "scope": "user",
                                    "enabled": True,
                                }
                            ],
                            "errors": [
                                {
                                    "path": "/private/broken/SKILL.md",
                                    "message": "/private/broken/SKILL.md frontmatter 无效",
                                }
                            ],
                        }
                    ]
                },
            }
        )
        yield Step.recv()

    manager = _manager(FakeAppServer(behavior()))

    catalog = await manager.list_skills(cwd="/tmp/x")

    assert catalog == {
        "skills": [
            {
                "name": "development-slice-workflow",
                "description": "推进开发 slice",
                "scope": "user",
                "enabled": True,
            }
        ],
        "errors": ["技能配置加载失败"],
    }
    assert "/private/broken" not in str(catalog)
    await manager.close()


def test_skill_catalog_malformed_result_keeps_protocol_error_type() -> None:
    """畸形目录必须保留协议错误及原始诊断 payload，不能二次抛 TypeError。"""

    malformed: object = []

    with pytest.raises(ProtocolViolationError) as captured:
        parse_skill_catalog(malformed, cwd="/tmp/x")

    assert captured.value.payload is malformed


async def test_compact_reserves_idle_session_and_sends_native_request() -> None:
    fake = FakeAppServer(_behavior_server())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.compact(session)

    compact = next(msg for msg in fake.received if msg.get("method") == "thread/compact/start")
    assert compact["params"] == {"threadId": "t-1"}
    with pytest.raises(TurnConflictError):
        session.begin_send()
    session.abort_send()
    await manager.close()


async def test_compact_native_turn_owns_reservation_until_terminal() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "compact-turn", "status": "inProgress", "items": []},
                },
            }
        )
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "compact-turn", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    manager = _manager(FakeAppServer(behavior()))
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.compact(session)

    with pytest.raises(TurnConflictError):
        session.begin_send()
    async with asyncio.timeout(1):
        async for event in session.events():
            if event.type is CodexEventType.TURN_STARTED:
                break
    assert event.turn_id == "compact-turn"
    assert event.payload["autonomous"] is True
    assert event.payload["memory_eligible"] is False
    assert session.state is CodexSessionState.RUNNING

    async with asyncio.timeout(1):
        async for event in session.events():
            if event.type is CodexEventType.FINISHED:
                break
    assert session.state is CodexSessionState.IDLE
    session.begin_send()
    session.abort_send()
    await manager.close()


async def test_compact_protocol_failure_releases_session_reservation() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send(
            {
                "id": msg["id"],
                "error": {"code": -32600, "message": "compact unavailable"},
            }
        )
        yield Step.recv()

    manager = _manager(FakeAppServer(behavior()))
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    with pytest.raises(ProtocolViolationError, match="compact unavailable"):
        await manager.compact(session)

    session.begin_send()
    session.abort_send()
    await manager.close()


async def test_review_uses_schema_target_and_starts_autonomous_turn_without_user() -> None:
    captured: list[dict] = []

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        captured.append(msg)
        yield Step.send(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "review-turn", "status": "inProgress", "items": []},
                },
            }
        )
        yield Step.send(
            {
                "id": msg["id"],
                "result": {
                    "reviewThreadId": "t-1",
                    "turn": {"id": "review-turn", "status": "inProgress", "items": []},
                },
            }
        )
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "review-turn", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    result = await manager.start_review(
        session,
        {"type": "commit", "sha": "abc123", "title": "Fix auth"},
    )

    assert captured[0]["method"] == "review/start"
    assert captured[0]["params"] == {
        "threadId": "t-1",
        "target": {"type": "commit", "sha": "abc123", "title": "Fix auth"},
        "delivery": "inline",
    }
    assert result == {"review_thread_id": "t-1", "turn_id": "review-turn"}
    started = session.drain()
    assert any(
        event.type is CodexEventType.TURN_STARTED
        and event.turn_id == "review-turn"
        and event.payload["autonomous"] is True
        and event.payload["memory_eligible"] is False
        for event in started
    )
    assert not any(event.type is CodexEventType.USER for event in started)

    await asyncio.sleep(0.05)
    assert any(event.type is CodexEventType.FINISHED for event in session.drain())
    await manager.close()


async def test_review_protocol_failure_releases_session_reservation() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"reviewThreadId": "t-1"}})
        yield Step.recv()

    manager = _manager(FakeAppServer(behavior()))
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    with pytest.raises(ProtocolViolationError):
        await manager.start_review(session, {"type": "uncommittedChanges"})

    session.begin_send()
    session.abort_send()
    await manager.close()
