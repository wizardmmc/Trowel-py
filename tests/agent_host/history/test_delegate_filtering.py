from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.agent_host.hub._support import (
    FakeCodexManager,
    cc_req,
    codex_req,
    make_cc_opener,
)
from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.store import BindingStore
from trowel_py.cc_host.session_scan import SessionSummary
from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)


async def _consume(stream: Any) -> None:
    """消费完整事件流，使原生会话身份完成写回。"""

    async for _ in stream:
        pass


@pytest.mark.asyncio
async def test_delegate_history_stays_hidden_after_cleanup_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings_path = tmp_path / "agent_sessions.json"
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    registry: dict[str, Any] = {}
    manager = FakeCodexManager()
    hub = SessionHub(
        BindingStore(bindings_path),
        codex_manager=manager,
        cc_registry=registry,
        cc_opener=make_cc_opener(registry, {}),
        codex_config_home=tmp_path,
    )

    cc_delegate = hub.create(cc_req(workdir, session_kind="delegate"))
    cc_native_id = "00000000-0000-4000-8000-000000000421"
    registry[cc_delegate.session_id].cc_session_id = cc_native_id
    await _consume(hub.stream(cc_delegate.session_id, "review"))

    codex_delegate = hub.create(codex_req(workdir, session_kind="delegate"))
    await hub.start_codex_turn(codex_delegate.session_id, "review")
    codex_native_id = "thread-1"
    codex_session = manager.get_session(codex_delegate.session_id)
    codex_session.emit_translated(
        TranslatedItem(
            type=CodexEventType.FINISHED,
            thread_id=codex_native_id,
            turn_id="fake-turn-id",
            payload=immutable_payload(status="completed"),
        )
    )

    assert await hub.delete(cc_delegate.session_id) is True
    assert await hub.delete(codex_delegate.session_id) is True
    assert BindingStore(bindings_path).list_all() == []

    restarted_manager = FakeCodexManager()
    restarted_manager.threads = [
        {
            "id": codex_native_id,
            "name": "internal codex review",
            "updatedAt": 90,
        },
        {
            "id": "external-codex",
            "name": "ordinary external codex session",
            "updatedAt": 70,
        },
    ]
    restarted_hub = SessionHub(
        BindingStore(bindings_path),
        codex_manager=restarted_manager,
        cc_registry={},
        cc_opener=make_cc_opener({}, {}),
        codex_config_home=tmp_path,
    )
    cc_rows = [
        SessionSummary(
            cc_session_id=cc_native_id,
            title="internal cc review",
            updated_at=100,
        ),
        SessionSummary(
            cc_session_id="00000000-0000-4000-8000-000000000999",
            title="ordinary external cc session",
            updated_at=80,
        ),
    ]

    def scan_cc(
        _workdir: str,
        *,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[SessionSummary]:
        return [row for row in cc_rows if row.cc_session_id not in excluded_ids][:limit]

    monkeypatch.setattr(
        "trowel_py.agent_host.history.scan_cc_history",
        scan_cc,
    )

    rows, next_cursor = await restarted_hub.list_history(
        str(workdir),
        limit=2,
        cursor=None,
    )

    assert [(row["runtime"], row["native_session_id"]) for row in rows] == [
        ("claude_code", "00000000-0000-4000-8000-000000000999"),
        ("codex", "external-codex"),
    ]
    assert next_cursor is None


@pytest.mark.asyncio
async def test_delete_preserves_last_delegate_identity_when_index_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    registry: dict[str, Any] = {}
    store = BindingStore(tmp_path / "agent_sessions.json")
    hub = SessionHub(
        store,
        cc_registry=registry,
        cc_opener=make_cc_opener(registry, {}),
        codex_config_home=tmp_path,
    )
    delegate = hub.create(cc_req(workdir, session_kind="delegate"))
    store.update_native(delegate.session_id, native_session_id="delegate-native")

    def fail_add(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("index unavailable")

    monkeypatch.setattr(hub._delegate_identities, "add", fail_add)  # noqa: SLF001

    with pytest.raises(OSError, match="index unavailable"):
        await hub.delete(delegate.session_id)

    assert store.get(delegate.session_id) is not None
    assert registry[delegate.session_id].closed is False
