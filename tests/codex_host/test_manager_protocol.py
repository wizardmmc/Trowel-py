from __future__ import annotations


from trowel_py.codex_host import (
    CodexHostManager,
    CodexSession,
    CodexSessionConfig,
)
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host._manager_support import (
    _init_resp,
    _manager,
    _model_list_fixture,
)


async def test_model_list_paginates_and_preserves_native_order() -> None:

    recorded = _model_list_fixture()

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        first = yield Step.recv()
        assert first["method"] == "model/list"
        assert first["params"] == {"includeHidden": False}
        yield Step.send(
            {
                "id": first["id"],
                "result": {"data": recorded["data"][:2], "nextCursor": "page-2"},
            }
        )
        second = yield Step.recv()
        assert second["params"] == {"includeHidden": False, "cursor": "page-2"}
        yield Step.send(
            {
                "id": second["id"],
                "result": {"data": recorded["data"][2:], "nextCursor": None},
            }
        )
        yield Step.hold(0.05)

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    models = await manager.list_models()
    assert [row["id"] for row in models] == [row["id"] for row in recorded["data"]]
    assert [e["value"] for e in models[0]["supported_efforts"]] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    await manager.close()


def test_follow_thread_start_omits_permission_overrides() -> None:

    manager = CodexHostManager()
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="follow",
            workdir="/tmp/x",
            approval_policy=None,
            sandbox=None,
        )
    )
    params = manager._thread_start_params(session)  # noqa: SLF001
    assert "approvalPolicy" not in params
    assert "sandbox" not in params


def test_turn_start_carries_model_and_effort_as_one_selection() -> None:

    params = CodexHostManager._turn_start_params(  # noqa: SLF001
        "thr-1", "hello", model="gpt-future", effort="ultra"
    )
    assert params["model"] == "gpt-future"
    assert params["effort"] == "ultra"
