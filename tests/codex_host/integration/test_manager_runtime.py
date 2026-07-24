"""真实 Codex app-server 的 Manager、Translator 与 thread/turn 集成 smoke。

测试默认由 integration marker 筛除，且只在 ``CODEX_INTEGRATION=1`` 时运行；测试
代码不直接读取认证文件，子进程继承当前环境和 Codex 登录。

执行方式::

    CODEX_INTEGRATION=1 .venv/bin/python -m pytest -m integration \\
        tests/codex_host/integration/test_manager_runtime.py

覆盖 ``thread/start``、``turn/start``、通知流、并发 thread 隔离和重启后的
``thread/resume``；这些 smoke 只验证真实 app-server shape 与运行时调用链一致。
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
    """只认 turn 终态；``ensure_ready`` 广播的 READY ``HOST_STATUS`` 不结束 turn。"""

    terminal = {
        CodexEventType.FINISHED,
        CodexEventType.INTERRUPTED,
        CodexEventType.ERROR,
    }
    return any(event.type in terminal for event in events)


async def test_real_manager_send_completes_one_turn() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-manager-"))
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
    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-manager-pair-"))
    try:
        manager = CodexHostManager()
        session_a = CodexSession(CodexSessionConfig("sA", str(workdir), ephemeral=True))
        session_b = CodexSession(CodexSessionConfig("sB", str(workdir), ephemeral=True))
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
            # 不同 thread 的事件不得串线。
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
    """真实 app-server 接受 ``turn/start.effort``；``thread/start`` 没有该字段。"""

    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-manager-effort-"))
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
    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-manager-resume-"))
    try:
        mgr1 = CodexHostManager()
        session = CodexSession(CodexSessionConfig("s1", str(workdir), ephemeral=False))
        mgr1.register(session)
        try:
            await mgr1.send(
                session, "Remember the marker TROWEL_CODEX_RESUME_MARKER. Reply: ok"
            )
            events1: list[CodexEvent] = []
            await _drain_until(session, stop=_finished, events=events1)
            assert session.binding is not None
            thread_id = session.binding.thread_id
        finally:
            await mgr1.close()

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
            # 持久 thread 尽力归档，清理失败不能覆盖主断言。
            client = mgr2.client
            if client is not None and not client.closed:
                try:
                    await client.request("thread/archive", {"threadId": thread_id})
                except Exception:  # noqa: BLE001
                    pass
            await mgr2.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
