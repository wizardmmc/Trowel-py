from __future__ import annotations

from pathlib import Path

import pytest

from tests.agent_host.hub._support import FakeCodexManager, make_cc_opener
from trowel_py.agent_host.history import decode_history_cursor
from trowel_py.agent_host.hub import InvalidSessionRequestError, SessionHub
from trowel_py.agent_host.store import BindingStore
from trowel_py.cc_host.session_scan import SessionSummary


@pytest.fixture
def history_hub(tmp_path: Path) -> tuple[SessionHub, FakeCodexManager]:
    manager = FakeCodexManager()
    registry = {}
    hub = SessionHub(
        BindingStore(tmp_path / "bindings.json"),
        codex_manager=manager,
        cc_registry=registry,
        cc_opener=make_cc_opener(registry, {}),
        codex_config_home=tmp_path,
    )
    return hub, manager


async def test_history_pages_merge_both_runtimes_in_global_updated_order(
    history_hub: tuple[SessionHub, FakeCodexManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub, manager = history_hub
    cc = [
        SessionSummary(cc_session_id=f"cc-{index:02}", title=f"CC {index}", updated_at=index * 2)
        for index in range(25)
    ]
    manager.threads = [
        {
            "id": f"codex-{index:02}",
            "name": None,
            "preview": f"Codex {index}",
            "updatedAt": index * 2 + 1,
        }
        for index in range(15)
    ]
    monkeypatch.setattr(
        "trowel_py.agent_host.history.scan_cc_history",
        lambda _workdir, *, limit: sorted(cc, key=lambda row: row.updated_at, reverse=True)[:limit],
    )

    first, first_cursor = await hub.list_history("/workspace", limit=20, cursor=None)
    second, second_cursor = await hub.list_history(
        "/workspace", limit=20, cursor=first_cursor
    )

    combined = first + second
    assert len(first) == 20
    assert len(second) == 20
    assert second_cursor is None
    assert len({(row["runtime"], row["native_session_id"]) for row in combined}) == 40
    assert [row["updated_at"] for row in combined] == sorted(
        [row["updated_at"] for row in combined], reverse=True
    )
    assert {row["runtime"] for row in combined} == {"claude_code", "codex"}
    assert decode_history_cursor(first_cursor) == 20
    assert manager.list_thread_calls == [("/workspace", 21), ("/workspace", 41)]


async def test_history_rejects_invalid_cursor(
    history_hub: tuple[SessionHub, FakeCodexManager],
) -> None:
    hub, _ = history_hub
    with pytest.raises(InvalidSessionRequestError):
        await hub.list_history("/workspace", limit=20, cursor="not-a-cursor")
