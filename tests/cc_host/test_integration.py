"""使用真实 `claude -p` 子进程的 CC Host 集成测试。

默认不运行：测试依赖真实 claude binary 和可用的 GLM backend，且不能在交互式
claude 会话内执行；嵌套 `claude -p` 会因 #46416 死锁并以 124 退出。

在普通终端运行：
    CC_INTEGRATION=1 .venv/bin/python -m pytest -m integration \
        tests/cc_host/test_integration.py
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import (
    FinishedEvent,
    SessionStartedEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)

pytestmark = pytest.mark.integration

_INTEGRATION_ON = bool(os.environ.get("CC_INTEGRATION"))
_HAS_CLAUDE = shutil.which("claude") is not None
_SKIP_REASON = (
    "set CC_INTEGRATION=1 and run from a plain terminal (not inside claude; #46416)"
)
skip = pytest.mark.skipif(not (_INTEGRATION_ON and _HAS_CLAUDE), reason=_SKIP_REASON)


async def _events(host: CCHost, text: str) -> list:
    return [e async for e in host.send(text)]


@skip
async def test_single_turn_text(tmp_path: Path):
    host = CCHost("s", tmp_path)
    events = await _events(host, "reply with a single digit: 1")
    kinds = [type(e).__name__ for e in events]
    assert "SessionStartedEvent" in kinds
    assert any(isinstance(e, TextEvent) for e in events)
    assert isinstance(events[-1], FinishedEvent)
    await host.close()


@skip
async def test_coding_writes_file(tmp_path: Path):
    host = CCHost("s", tmp_path)
    target = tmp_path / "hello.txt"
    events = await _events(host, f"create the file {target} with content 'hi'")
    assert any(isinstance(e, ToolCallEvent) for e in events)
    assert any(isinstance(e, ToolResultEvent) for e in events)
    assert target.exists()
    await host.close()


@skip
async def test_skill_explicit_trigger(tmp_path: Path):
    # system/init.tools 必须包含 Skill；/name 会转换为触发 prompt。
    host = CCHost("s", tmp_path)
    events = await _events(host, "/test-skill")
    init = next(e for e in events if isinstance(e, SessionStartedEvent))
    assert "Skill" in init.tools
    # 模型必须实际调用 Skill tool。
    assert any(isinstance(e, ToolCallEvent) and e.tool_name == "Skill" for e in events)
    await host.close()


@skip
async def test_custom_command_expansion(tmp_path: Path):
    cmd_dir = tmp_path / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "echo.md").write_text(
        "---\ndescription: echo\n---\nRespond with exactly: ECHO$ARGUMENTS\n"
    )
    host = CCHost("s", tmp_path)
    events = await _events(host, "/echo hello")
    text = "".join(e.text for e in events if isinstance(e, TextEvent))
    assert "ECHOhello" in text
    await host.close()


@skip
async def test_interrupt_then_resume(tmp_path: Path):
    host = CCHost("s", tmp_path, effort="high")
    # 先启动长任务，再在执行中中断。
    import asyncio

    task = asyncio.create_task(_events(host, "explain Shannon entropy in great detail"))
    await asyncio.sleep(8)
    await host.interrupt()
    try:
        await task
    except Exception:
        pass
    # 进程已结束；下一次 send 必须惰性恢复。
    events = await _events(host, "reply with a single digit: 1")
    assert any(isinstance(e, FinishedEvent) for e in events)
    await host.close()
