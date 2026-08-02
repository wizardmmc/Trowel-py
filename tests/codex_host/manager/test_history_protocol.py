from __future__ import annotations

import pytest

from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import _init_resp, _manager
from trowel_py.codex_host.errors import ProtocolViolationError


async def test_thread_list_and_read_use_public_app_server_contract() -> None:
    thread = {
        "id": "thread-1",
        "cwd": "/workspace",
        "preview": "title",
        "updatedAt": 20,
        "turns": [],
    }
    older = {**thread, "id": "thread-2", "updatedAt": 10}

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()

        listed = yield Step.recv()
        assert listed["method"] == "thread/list"
        assert listed["params"] == {
            "cwd": "/workspace",
            "limit": 41,
            "sortKey": "updated_at",
            "sortDirection": "desc",
        }
        yield Step.send(
            {
                "id": listed["id"],
                "result": {"data": [thread], "nextCursor": "page-2"},
            }
        )

        next_page = yield Step.recv()
        assert next_page["method"] == "thread/list"
        assert next_page["params"] == {
            "cwd": "/workspace",
            "limit": 40,
            "sortKey": "updated_at",
            "sortDirection": "desc",
            "cursor": "page-2",
        }
        yield Step.send(
            {
                "id": next_page["id"],
                "result": {"data": [older], "nextCursor": None},
            }
        )

        read = yield Step.recv()
        assert read["method"] == "thread/read"
        assert read["params"] == {"threadId": "thread-1", "includeTurns": True}
        yield Step.send({"id": read["id"], "result": {"thread": thread}})
        yield Step.hold(0.05)

    fake = FakeAppServer(behavior())
    manager = _manager(fake)

    assert await manager.list_threads(cwd="/workspace", limit=41) == [thread, older]
    assert await manager.read_thread("thread-1") == thread
    await manager.close()


async def test_thread_list_keeps_paging_until_exclusions_are_replaced() -> None:
    delegate_new = {
        "id": "delegate-new",
        "cwd": "/workspace",
        "preview": "internal",
        "updatedAt": 40,
        "turns": [],
    }
    external_new = {**delegate_new, "id": "external-new", "updatedAt": 30}
    delegate_old = {**delegate_new, "id": "delegate-old", "updatedAt": 20}
    external_old = {**delegate_new, "id": "external-old", "updatedAt": 10}

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()

        first = yield Step.recv()
        assert first["params"]["limit"] == 2
        yield Step.send(
            {
                "id": first["id"],
                "result": {
                    "data": [delegate_new, external_new],
                    "nextCursor": "page-2",
                },
            }
        )

        second = yield Step.recv()
        assert second["params"]["limit"] == 1
        assert second["params"]["cursor"] == "page-2"
        yield Step.send(
            {
                "id": second["id"],
                "result": {"data": [delegate_old], "nextCursor": "page-3"},
            }
        )

        third = yield Step.recv()
        assert third["params"]["limit"] == 1
        assert third["params"]["cursor"] == "page-3"
        yield Step.send(
            {
                "id": third["id"],
                "result": {"data": [external_old], "nextCursor": None},
            }
        )
        yield Step.hold(0.05)

    manager = _manager(FakeAppServer(behavior()))

    rows = await manager.list_threads(
        cwd="/workspace",
        limit=2,
        excluded_ids=frozenset({"delegate-new", "delegate-old"}),
    )

    assert [row["id"] for row in rows] == ["external-new", "external-old"]
    await manager.close()


@pytest.mark.parametrize("next_cursor", [7, {"page": 2}])
async def test_thread_list_rejects_non_string_cursor(next_cursor: object) -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        listed = yield Step.recv()
        yield Step.send(
            {
                "id": listed["id"],
                "result": {"data": [], "nextCursor": next_cursor},
            }
        )
        yield Step.hold(0.05)

    manager = _manager(FakeAppServer(behavior()))

    with pytest.raises(ProtocolViolationError, match="nextCursor"):
        await manager.list_threads(cwd="/workspace", limit=20)
    await manager.close()


async def test_thread_list_rejects_repeated_cursor() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        first = yield Step.recv()
        yield Step.send(
            {
                "id": first["id"],
                "result": {"data": [], "nextCursor": "same"},
            }
        )
        second = yield Step.recv()
        yield Step.send(
            {
                "id": second["id"],
                "result": {"data": [], "nextCursor": "same"},
            }
        )
        yield Step.hold(0.05)

    manager = _manager(FakeAppServer(behavior()))

    with pytest.raises(ProtocolViolationError, match="repeated cursor"):
        await manager.list_threads(cwd="/workspace", limit=20)
    await manager.close()
