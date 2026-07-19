"""Real Codex app-server integration smoke for slice-071.

Deselected by default (``-m 'not integration'``) and additionally env-gated
behind ``CODEX_INTEGRATION=1``. It uses the current Codex subscription login
and the real ``codex app-server`` — never reads ``~/.codex/auth.json``, never
touches stable.

Run::

    CODEX_INTEGRATION=1 .venv/bin/python -m pytest -m integration \\
        tests/codex_host/test_integration_071.py

These tests exercise the slice-071 orchestration end-to-end: ``thread/start``
→ ``turn/start`` → notification stream → ``turn/completed``, concurrent
thread isolation, and ``thread/resume`` after a manager restart. The fake in
``test_manager.py`` already pins the routing logic deterministically; what
these add is confidence that the real app-server's response shapes match what
the manager + translator expect (spec C-7 — real fixtures, no guessing).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

import pytest

from trowel_py.codex_host import (
    CodexEvent,
    CodexEventType,
    CodexHostManager,
    CodexSession,
    CodexSessionConfig,
)

_ENV_GATE = "CODEX_INTEGRATION"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get(_ENV_GATE) != "1",
        reason=f"set {_ENV_GATE}=1 to run the real Codex app-server smoke",
    ),
]


async def _drain_until(
    session: CodexSession,
    *,
    stop,
    events: list[CodexEvent],
    timeout_s: float = 120.0,
) -> None:
    """Drain ``session`` into ``events`` until ``stop(events)`` is true or timeout."""

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        events.extend(session.drain())
        if stop(events):
            return
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"timed out waiting for turn to finish on session {session.session_id}"
    )


def _finished(events: Iterable[CodexEvent]) -> bool:
    """True once the turn reached a turn-level terminal event.

    Only FINISHED / INTERRUPTED / ERROR count — HOST_STATUS is excluded
    because the manager broadcasts a READY host-status right after
    ``ensure_ready``, and that initial flip is not a turn terminal.
    """

    terminal = {
        CodexEventType.FINISHED,
        CodexEventType.INTERRUPTED,
        CodexEventType.ERROR,
    }
    return any(event.type in terminal for event in events)


async def test_real_manager_send_completes_one_turn() -> None:
    """One session: send → SESSION_STARTED/USER/TURN_STARTED/assistant/FINISHED."""

    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-071-"))
    try:
        manager = CodexHostManager()
        session = CodexSession(
            CodexSessionConfig(
                trowel_session_id="s1", workdir=str(workdir), ephemeral=True
            )
        )
        manager.register(session)
        try:
            await manager.send(session, "Reply with the single word: pong")
            events: list[CodexEvent] = []
            await _drain_until(session, stop=_finished, events=events)
            types = {event.type for event in events}
            assert CodexEventType.SESSION_STARTED in types
            assert CodexEventType.TURN_STARTED in types
            assert CodexEventType.FINISHED in types
            assert session.binding is not None
            assert session.binding.model_provider == "openai"
        finally:
            await manager.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_real_two_sessions_isolated() -> None:
    """Two concurrent sessions on one manager: each finishes on its own thread."""

    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-071-2-"))
    try:
        manager = CodexHostManager()
        session_a = CodexSession(
            CodexSessionConfig("sA", str(workdir), ephemeral=True)
        )
        session_b = CodexSession(
            CodexSessionConfig("sB", str(workdir), ephemeral=True)
        )
        manager.register(session_a)
        manager.register(session_b)
        try:
            await asyncio.gather(
                manager.send(session_a, "Reply with: alpha"),
                manager.send(session_b, "Reply with: beta"),
            )
            events_a: list[CodexEvent] = []
            events_b: list[CodexEvent] = []
            await asyncio.gather(
                _drain_until(session_a, stop=_finished, events=events_a),
                _drain_until(session_b, stop=_finished, events=events_b),
            )
            # Different threads, no event crossed over.
            assert session_a.thread_id != session_b.thread_id
            for event in events_a:
                if event.thread_id is not None:
                    assert event.thread_id == session_a.thread_id
            for event in events_b:
                if event.thread_id is not None:
                    assert event.thread_id == session_b.thread_id
            assert any(event.type is CodexEventType.FINISHED for event in events_a)
            assert any(event.type is CodexEventType.FINISHED for event in events_b)
        finally:
            await manager.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_real_effort_accepted_on_turn_start() -> None:
    """effort on turn/start is accepted by the real app-server.

    Guards the codex review P2: ThreadStartParams has no ``effort`` field, so
    sending it on thread/start could be rejected; this confirms the override
    rides on turn/start and the turn completes.
    """

    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-071-effort-"))
    try:
        manager = CodexHostManager()
        session = CodexSession(
            CodexSessionConfig("s1", str(workdir), effort="high", ephemeral=True)
        )
        manager.register(session)
        try:
            await manager.send(session, "Reply with: ok")
            events: list[CodexEvent] = []
            await _drain_until(session, stop=_finished, events=events)
            assert any(event.type is CodexEventType.FINISHED for event in events)
        finally:
            await manager.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_real_resume_after_restart() -> None:
    """End the manager, start a fresh one, resume the same thread, finish a turn."""

    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-071-resume-"))
    try:
        # Turn 1 on the first manager (non-ephemeral so the thread persists).
        mgr1 = CodexHostManager()
        session = CodexSession(
            CodexSessionConfig("s1", str(workdir), ephemeral=False)
        )
        mgr1.register(session)
        try:
            await mgr1.send(session, "Remember the marker TROWEL_071_RESUME. Reply: ok")
            events1: list[CodexEvent] = []
            await _drain_until(session, stop=_finished, events=events1)
            assert session.binding is not None
            thread_id = session.binding.thread_id
        finally:
            await mgr1.close()

        # New manager process; the session kept the binding, so the next send
        # must resume the same thread (not start a fresh one).
        mgr2 = CodexHostManager()
        mgr2.register(session)
        try:
            await mgr2.send(session, "What marker did I ask you to remember?")
            events2: list[CodexEvent] = []
            await _drain_until(session, stop=_finished, events=events2)
            assert session.binding is not None
            assert session.binding.thread_id == thread_id
            assert any(event.type is CodexEventType.FINISHED for event in events2)
        finally:
            # Archive the persisted thread so the smoke leaves no residue.
            client = mgr2.client
            if client is not None and not client.closed:
                try:
                    await client.request("thread/archive", {"threadId": thread_id})
                except Exception:  # noqa: BLE001 — archive is best-effort cleanup
                    pass
            await mgr2.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
