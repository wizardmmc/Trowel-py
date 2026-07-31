from __future__ import annotations

import asyncio

import pytest

from tests.agent_host.hub._support import (
    FakeCodexManager,
    cc_req,
    make_cc_opener,
)
from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.hub import SessionHub, SessionOperationError
from trowel_py.agent_host.store import BindingStore
from trowel_py.agent_host.title_store import SessionTitleStore
from trowel_py.agent_host.title_store import SessionTitleRecord


class FakeTitleGenerator:
    def __init__(self, result: str = "设计会话标题") -> None:
        self.result = result
        self.calls: list[tuple[Runtime, str, str]] = []

    async def generate(self, runtime: Runtime, text: str, workdir: str) -> str:
        self.calls.append((runtime, text, workdir))
        return self.result


class WaitingTitleGenerator(FakeTitleGenerator):
    def __init__(self) -> None:
        super().__init__("迟到的自动标题")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, runtime: Runtime, text: str, workdir: str) -> str:
        self.calls.append((runtime, text, workdir))
        self.started.set()
        await self.release.wait()
        return self.result


def make_hub(tmp_path, generator):
    registry = {}
    store = BindingStore(tmp_path / "bindings.json")
    title_store = SessionTitleStore(tmp_path / "titles.json")
    hub = SessionHub(
        store,
        codex_manager=FakeCodexManager(),
        cc_registry=registry,
        cc_opener=make_cc_opener(registry, {}),
        codex_config_home=tmp_path,
        title_generator=generator,
        title_store=title_store,
    )
    return hub, title_store


@pytest.mark.asyncio
async def test_first_prompt_is_persisted_before_generated_title_returns(tmp_path):
    generator = WaitingTitleGenerator()
    hub, _ = make_hub(tmp_path, generator)
    workdir = tmp_path / "project"
    workdir.mkdir()
    binding = hub.create(cc_req(workdir))

    task = asyncio.create_task(
        hub.generate_title(binding.session_id, "  设计   目录分组\n和语义标题 ")
    )
    await generator.started.wait()

    pending = hub.get(binding.session_id)
    assert pending is not None
    assert pending.display_title == "设计 目录分组 和语义标题"
    assert pending.title_source == "prompt"

    generator.release.set()
    generated = await task
    assert generated.display_title == "迟到的自动标题"
    assert generated.title_source == "generated"


@pytest.mark.asyncio
async def test_manual_title_wins_when_generation_finishes_late(tmp_path):
    generator = WaitingTitleGenerator()
    hub, _ = make_hub(tmp_path, generator)
    workdir = tmp_path / "project"
    workdir.mkdir()
    binding = hub.create(cc_req(workdir))

    task = asyncio.create_task(hub.generate_title(binding.session_id, "原始提示词"))
    await generator.started.wait()
    renamed = hub.rename_title(binding.session_id, "我定的标题")
    generator.release.set()
    finished = await task

    assert renamed.title_source == "manual"
    assert finished.display_title == "我定的标题"
    assert hub.get(binding.session_id).display_title == "我定的标题"


@pytest.mark.asyncio
async def test_second_prompt_cannot_replace_the_first_prompt_title(tmp_path):
    generator = WaitingTitleGenerator()
    hub, _ = make_hub(tmp_path, generator)
    workdir = tmp_path / "project"
    workdir.mkdir()
    binding = hub.create(cc_req(workdir))

    first = asyncio.create_task(
        hub.generate_title(binding.session_id, "第一条真实提示词")
    )
    await generator.started.wait()
    second = await hub.generate_title(binding.session_id, "另一条后续提示词")
    generator.release.set()
    finished = await first

    assert len(generator.calls) == 1
    assert second.display_title == "第一条真实提示词"
    assert finished.display_title == "迟到的自动标题"


@pytest.mark.asyncio
async def test_generated_title_is_indexed_and_restored_after_binding_deleted(tmp_path):
    generator = FakeTitleGenerator()
    hub, title_store = make_hub(tmp_path, generator)
    workdir = tmp_path / "project"
    workdir.mkdir()
    binding = hub.create(cc_req(workdir))
    hub.store.update_native(binding.session_id, native_session_id="native-1")

    await hub.generate_title(binding.session_id, "设计目录分组")

    saved = title_store.get(Runtime.CLAUDE_CODE, "native-1")
    assert saved is not None
    assert saved.title == "设计会话标题"
    hub.store.delete(binding.session_id)

    restored = hub.create(
        cc_req(
            workdir,
            resume_from="native-1",
            resume_title="原生旧标题",
        )
    )
    assert restored.display_title == "设计会话标题"
    assert restored.title_source == "generated"


def test_long_native_title_does_not_block_resuming_session(tmp_path):
    hub, _ = make_hub(tmp_path, FakeTitleGenerator())
    workdir = tmp_path / "project"
    workdir.mkdir()

    restored = hub.create(
        cc_req(
            workdir,
            resume_from="native-1",
            resume_title="原生标题" * 100,
        )
    )

    assert restored.display_title.startswith("原生标题")
    assert restored.display_title.endswith("…")
    assert len(restored.display_title) == 80
    assert restored.title_source == "native"


@pytest.mark.asyncio
async def test_generator_failure_keeps_prompt_fallback(tmp_path):
    class Broken:
        async def generate(self, runtime, text, workdir):
            raise TimeoutError("slow")

    hub, _ = make_hub(tmp_path, Broken())
    workdir = tmp_path / "project"
    workdir.mkdir()
    binding = hub.create(cc_req(workdir))

    result = await hub.generate_title(binding.session_id, "排查标题超时")

    assert result.display_title == "排查标题超时"
    assert result.title_source == "prompt"


@pytest.mark.asyncio
async def test_delegate_cannot_generate_or_rename_title(tmp_path):
    hub, _ = make_hub(tmp_path, FakeTitleGenerator())
    workdir = tmp_path / "project"
    workdir.mkdir()
    binding = hub.create(cc_req(workdir, session_kind="delegate"))

    with pytest.raises(SessionOperationError):
        await hub.generate_title(binding.session_id, "内部任务")
    with pytest.raises(SessionOperationError):
        hub.rename_title(binding.session_id, "内部标题")


@pytest.mark.asyncio
async def test_history_prefers_persisted_title_over_native_title(tmp_path):
    hub, title_store = make_hub(tmp_path, FakeTitleGenerator())
    workdir = tmp_path / "project"
    workdir.mkdir()
    hub._codex.threads = [
        {
            "id": "thread-1",
            "name": "Codex 原生标题",
            "updatedAt": 10,
        }
    ]
    title_store.put(
        SessionTitleRecord(
            runtime=Runtime.CODEX,
            native_session_id="thread-1",
            title="手动保留的标题",
            source="manual",
            updated_at="2026-07-31T10:00:00",
        )
    )

    rows, next_cursor = await hub.list_history(
        str(workdir),
        limit=20,
        cursor=None,
    )

    assert next_cursor is None
    assert rows == [
        {
            "runtime": "codex",
            "native_session_id": "thread-1",
            "title": "手动保留的标题",
            "title_source": "manual",
            "updated_at": 10,
        }
    ]


def test_delegate_resume_does_not_restore_user_facing_title(tmp_path):
    hub, title_store = make_hub(tmp_path, FakeTitleGenerator())
    workdir = tmp_path / "project"
    workdir.mkdir()
    title_store.put(
        SessionTitleRecord(
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="delegate-native",
            title="不应展示",
            source="generated",
            updated_at="2026-07-31T10:00:00",
        )
    )

    binding = hub.create(
        cc_req(
            workdir,
            resume_from="delegate-native",
            session_kind="delegate",
        )
    )

    assert binding.display_title == ""
    assert binding.title_source == "new"
