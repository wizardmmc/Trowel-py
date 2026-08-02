from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.session_titles import (
    CLAUDE_TITLE_MODEL,
    CODEX_TITLE_MODEL,
    NativeSessionTitleGenerator,
    TITLE_TIMEOUT_S,
    build_claude_title_args,
    clean_generated_title,
    prompt_title,
)
from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.resource_lifecycle import ProcessIdentity, ResourceRegistry


def test_prompt_title_collapses_whitespace_and_bounds_fallback():
    text = "  排查   SSE\n断链  " + "很长" * 80

    title = prompt_title(text)

    assert title.startswith("排查 SSE 断链")
    assert title.endswith("…")
    assert len(title) <= 80


def test_generated_title_removes_wrappers_but_rejects_explanations():
    assert clean_generated_title('  “实现目录分组”  ') == "实现目录分组"
    assert clean_generated_title("标题：实现目录分组") == "实现目录分组"
    assert clean_generated_title('{"title":"实现目录分组"}') == "实现目录分组"
    assert clean_generated_title("实现目录分组\n这是解释") is None
    assert clean_generated_title("x") is None
    assert clean_generated_title("x" * 49) is None


def test_claude_title_command_is_ephemeral_low_cost_and_tool_free():
    args = build_claude_title_args("输入")

    assert args[0]
    assert args[1:3] == ["-p", "--no-session-persistence"]
    assert args[args.index("--model") + 1] == CLAUDE_TITLE_MODEL == "haiku"
    assert args[args.index("--effort") + 1] == "low"
    assert args[args.index("--tools") + 1] == ""
    assert "--json-schema" in args


def test_runtime_models_are_frozen_for_title_generation():
    assert CLAUDE_TITLE_MODEL == "haiku"
    assert CODEX_TITLE_MODEL == "gpt-5.6-luna"
    assert TITLE_TIMEOUT_S == 60.0
    assert Runtime.CLAUDE_CODE.value == "claude_code"


@pytest.mark.asyncio
async def test_claude_timeout_kills_process_and_falls_back_from_missing_pid():
    class FakeProcess:
        returncode = None

        def __init__(self) -> None:
            self.killed = False

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.returncode = -9
            return self.returncode

    process = FakeProcess()

    async def spawn(*_args, **_kwargs):
        return process

    generator = NativeSessionTitleGenerator(
        codex_manager=None,
        cc_proxy_base_url=None,
        cc_settings_path=None,
        timeout_s=0.001,
        subprocess_spawner=spawn,
    )

    with pytest.raises(TimeoutError, match="timed out"):
        await generator.generate(Runtime.CLAUDE_CODE, "生成标题", "/unused")

    assert process.killed is True


@pytest.mark.asyncio
async def test_cancelling_claude_title_kills_the_subprocess():
    class FakeProcess:
        returncode = None

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.killed = False

        async def communicate(self):
            self.started.set()
            await asyncio.Event().wait()

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.returncode = -9
            return self.returncode

    process = FakeProcess()

    async def spawn(*_args, **_kwargs):
        return process

    generator = NativeSessionTitleGenerator(
        codex_manager=None,
        cc_proxy_base_url=None,
        cc_settings_path=None,
        subprocess_spawner=spawn,
    )
    task = asyncio.create_task(
        generator.generate(Runtime.CLAUDE_CODE, "生成标题", "/unused")
    )
    await process.started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True


@pytest.mark.asyncio
async def test_claude_title_process_is_published_and_closed_in_resource_snapshot(
    tmp_path: Path,
) -> None:
    """标题进程必须进入 app 资源账本，正常结束后再标成 closed。"""

    class FakeProcess:
        """返回固定标题结果的独立进程组替身。"""

        pid = 811
        returncode = None

        async def communicate(self):
            """返回结构化标题，并模拟根进程正常退出。"""

            self.returncode = 0
            return b'{"structured_output":{"title":"resource title"}}', b""

    class Controller:
        """为标题进程提供固定启动身份和退出状态。"""

        def inspect(self, pid: int) -> ProcessIdentity | None:
            """只识别测试标题进程。"""

            return ProcessIdentity(pid, pid, "title-start") if pid == 811 else None

        def group_alive(self, process_group: int) -> bool:
            """根进程结束后视为整个测试进程组已经退出。"""

            return process_group == 811 and process.returncode is None

        def signal_group(self, _process_group: int, _signal_name: str) -> None:
            """正常完成路径不应发送信号。"""

            raise AssertionError("completed title process must not be signaled")

    process = FakeProcess()

    async def spawn(*_args, **_kwargs):
        """返回固定标题进程。"""

        return process

    snapshot_path = tmp_path / "resource-lifecycle.json"
    registry = ResourceRegistry(
        app_instance_id="app-title",
        snapshot_path=snapshot_path,
        process_controller=Controller(),
    )
    generator = NativeSessionTitleGenerator(
        codex_manager=None,
        cc_proxy_base_url=None,
        cc_settings_path=None,
        subprocess_spawner=spawn,
        resource_registry=registry,
    )

    title = await generator.generate(Runtime.CLAUDE_CODE, "生成标题", "/unused")

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    resources = [
        item
        for item in snapshot["resources"]
        if item["resource_kind"] == "session_title_process_group"
    ]
    assert title == "resource title"
    assert len(resources) == 1
    assert resources[0]["state"] == "closed"


@pytest.mark.asyncio
async def test_codex_title_uses_ephemeral_luna_without_project_or_trowel_mcp(
    tmp_path,
):
    class FakeManager:
        def __init__(self) -> None:
            self.session = None
            self.closed: list[tuple[str, bool]] = []
            self.unregistered: list[str] = []

        def register(self, session) -> None:
            self.session = session

        async def send(self, session, _text) -> None:
            assert Path(session.config.workdir).is_dir()
            session.begin_send()
            session.attach_thread_binding(
                {
                    "thread": {"id": "title-thread"},
                    "model": CODEX_TITLE_MODEL,
                    "modelProvider": "openai",
                    "cwd": session.config.workdir,
                    "sandbox": {"mode": "read-only"},
                    "approvalPolicy": "never",
                }
            )
            session.record_turn_started("turn-1", "title input")
            session.emit_translated(
                TranslatedItem(
                    type=CodexEventType.ASSISTANT_MESSAGE,
                    thread_id="title-thread",
                    turn_id="turn-1",
                    payload=immutable_payload(text='{"title":"按目录分组会话"}'),
                )
            )
            session.emit_translated(
                TranslatedItem(
                    type=CodexEventType.FINISHED,
                    thread_id="title-thread",
                    turn_id="turn-1",
                )
            )

        async def interrupt(self, _session) -> None:
            raise AssertionError("completed title must not be interrupted")

        async def close_session(self, session, *, preserve_history: bool) -> None:
            """记录临时 thread 已按原生关闭流程收敛。"""

            self.closed.append((session.session_id, preserve_history))

        def unregister(self, session_id: str) -> None:
            self.unregistered.append(session_id)

    project = tmp_path / "project"
    project.mkdir()
    manager = FakeManager()
    generator = NativeSessionTitleGenerator(
        codex_manager=manager,
        cc_proxy_base_url=None,
        cc_settings_path=None,
    )

    title = await generator.generate(
        Runtime.CODEX,
        "实现多开栏按目录聚合",
        str(project),
    )

    assert title == "按目录分组会话"
    config = manager.session.config
    assert config.model == CODEX_TITLE_MODEL
    assert config.effort == "low"
    assert config.ephemeral is True
    assert config.base_instructions
    assert config.developer_instructions is None
    assert config.trowel_memory_mcp is None
    assert config.trowel_agent_mcp is None
    assert config.workdir != str(project)
    assert not Path(config.workdir).exists()
    assert manager.closed == [(manager.session.session_id, False)]
    assert manager.unregistered == [manager.session.session_id]


@pytest.mark.asyncio
async def test_codex_timeout_interrupts_and_unregisters_ephemeral_session(tmp_path):
    class WaitingManager:
        def __init__(self) -> None:
            self.session = None
            self.interrupted = 0
            self.closed: list[tuple[str, bool]] = []
            self.unregistered: list[str] = []

        def register(self, session) -> None:
            self.session = session

        async def send(self, session, _text) -> None:
            session.begin_send()
            session.attach_thread_binding(
                {
                    "thread": {"id": "waiting-thread"},
                    "model": CODEX_TITLE_MODEL,
                    "modelProvider": "openai",
                    "cwd": session.config.workdir,
                    "sandbox": {"mode": "read-only"},
                    "approvalPolicy": "never",
                }
            )
            session.record_turn_started("turn-waiting", "title input")

        async def interrupt(self, _session) -> None:
            self.interrupted += 1

        async def close_session(self, session, *, preserve_history: bool) -> None:
            """记录超时 thread 已进入 archive 清理路径。"""

            self.closed.append((session.session_id, preserve_history))

        def unregister(self, session_id: str) -> None:
            self.unregistered.append(session_id)

    manager = WaitingManager()
    generator = NativeSessionTitleGenerator(
        codex_manager=manager,
        cc_proxy_base_url=None,
        cc_settings_path=None,
        timeout_s=0.001,
    )

    with pytest.raises(TimeoutError, match="timed out"):
        await generator.generate(Runtime.CODEX, "生成标题", str(tmp_path))

    assert manager.closed == [(manager.session.session_id, False)]
    assert manager.unregistered == [manager.session.session_id]
