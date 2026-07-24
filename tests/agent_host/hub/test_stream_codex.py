from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import (
    SessionHub,
)
from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    immutable_payload,
)
from tests.agent_host.hub._support import (
    FakeCodexManager,
    FakeCodexSession,
    _is_envelope,
    codex_req,
)


async def test_stream_codex_yields_unified_envelope(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
):

    binding = make_binding(
        session_id="codex-sess",
        runtime=Runtime.CODEX,
        native_session_id=None,
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="proj",
    )
    hub.store.put(binding)
    codex_events = [
        CodexEvent(
            session_id="codex-sess",
            seq=1,
            type=CodexEventType.SESSION_STARTED,
            thread_id="thr-1",
            payload=immutable_payload(model="gpt-5.6-sol", cwd=str(workdir)),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=2,
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="thr-1",
            turn_id="turn-1",
            item_id="item-1",
            payload=immutable_payload(delta="hello "),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=3,
            type=CodexEventType.FINISHED,
            thread_id="thr-1",
            turn_id="turn-1",
            payload=immutable_payload(status="completed"),
        ),
    ]
    session = FakeCodexSession("codex-sess", codex_events)
    codex_mgr.register(session)

    events = [e async for e in hub.stream("codex-sess", "hello")]
    assert all(_is_envelope(e) for e in events), events
    assert all(e["runtime"] == "codex" for e in events)

    types = [e["type"] for e in events]
    assert types == ["session_started", "text", "finished"], types

    text_ev = next(e for e in events if e["type"] == "text")
    assert text_ev["payload"]["text"] == "hello "
    assert text_ev["item_id"] == "item-1"


async def test_stream_codex_persists_binding_when_turn_start_fails(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
):

    binding = hub.create(codex_req(workdir), request=None)
    session = FakeCodexSession(
        binding.session_id,
        [],
        thread_id="thr-created-before-turn-failure",
    )
    codex_mgr.sessions[binding.session_id] = session

    # thread 已创建但 turn/start 失败时，必须先写回 native id 才能恢复。
    async def fail_after_thread_attached(
        session: Any,
        text: str,
        *,
        before_turn_start=None,
    ) -> str:

        del text
        if before_turn_start is not None:
            before_turn_start(session)
        raise RuntimeError("turn/start failed")

    monkeypatch.setattr(codex_mgr, "send", fail_after_thread_attached)

    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream(binding.session_id, "hello")]
    assert exc.value.status_code == 502
    persisted = hub.get(binding.session_id)
    assert persisted is not None
    assert persisted.native_session_id == "thr-created-before-turn-failure"


async def test_stream_codex_surfaces_writeback_failure_before_native_turn(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
):

    binding = hub.create(codex_req(workdir), request=None)
    codex_mgr.sessions[binding.session_id] = FakeCodexSession(
        binding.session_id,
        [],
        thread_id="thr-non-durable",
    )

    # native turn 开始前必须先持久化 thread binding。
    def fail_writeback(*args: Any, **kwargs: Any) -> None:

        del args, kwargs
        raise OSError("binding store unavailable")

    monkeypatch.setattr(hub.store, "update_native", fail_writeback)

    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream(binding.session_id, "hello")]
    assert exc.value.status_code == 502
    assert "binding store unavailable" in str(exc.value.detail)


async def test_stream_codex_stops_when_binding_becomes_unreadable_before_native_turn(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
):
    binding = hub.create(codex_req(workdir), request=None)
    session = FakeCodexSession(
        binding.session_id,
        [],
        thread_id="thr-without-readable-binding",
    )
    codex_mgr.sessions[binding.session_id] = session
    native_turn_started = False

    async def corrupt_binding_before_turn(
        session: Any,
        text: str,
        *,
        before_turn_start=None,
    ) -> str:
        nonlocal native_turn_started
        del text
        hub.store.path.write_text("{", encoding="utf-8")
        if before_turn_start is not None:
            before_turn_start(session)
        native_turn_started = True
        return "turn-without-binding"

    monkeypatch.setattr(codex_mgr, "send", corrupt_binding_before_turn)

    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream(binding.session_id, "hello")]

    assert exc.value.status_code == 502
    assert native_turn_started is False


async def test_stream_codex_stops_when_writeback_temporarily_reads_empty_binding(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
):
    binding = hub.create(codex_req(workdir), request=None)
    session = FakeCodexSession(
        binding.session_id,
        [],
        thread_id="thr-missed-by-writeback",
    )
    codex_mgr.sessions[binding.session_id] = session
    original_load = hub.store._load_raw  # noqa: SLF001
    first_read = True
    native_turn_started = False

    def read_empty_once() -> dict[str, dict[str, Any]]:
        nonlocal first_read
        if first_read:
            first_read = False
            return {}
        return original_load()

    async def start_after_callback(
        session: Any,
        text: str,
        *,
        before_turn_start=None,
    ) -> str:
        nonlocal native_turn_started
        del text
        monkeypatch.setattr(hub.store, "_load_raw", read_empty_once)
        if before_turn_start is not None:
            before_turn_start(session)
        native_turn_started = True
        return "turn-with-stale-binding"

    monkeypatch.setattr(codex_mgr, "send", start_after_callback)

    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream(binding.session_id, "hello")]

    assert exc.value.status_code == 502
    assert native_turn_started is False


async def test_stream_codex_finished_terminates_loop(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
):

    binding = make_binding(
        session_id="codex-sess",
        runtime=Runtime.CODEX,
        native_session_id=None,
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="proj",
    )
    hub.store.put(binding)

    # finished 后队列中的尾部事件不得泄漏到同一 turn。
    codex_events = [
        CodexEvent(
            session_id="codex-sess",
            seq=1,
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="thr-1",
            turn_id="turn-1",
            item_id="item-1",
            payload=immutable_payload(delta="hi"),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=2,
            type=CodexEventType.FINISHED,
            thread_id="thr-1",
            turn_id="turn-1",
            payload=immutable_payload(status="completed"),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=3,
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="thr-1",
            turn_id="turn-1",
            item_id="item-2",
            payload=immutable_payload(delta="should not appear"),
        ),
    ]
    session = FakeCodexSession("codex-sess", codex_events)
    codex_mgr.register(session)

    events = [e async for e in hub.stream("codex-sess", "hi")]
    types = [e["type"] for e in events]
    assert types == ["text", "finished"], types
