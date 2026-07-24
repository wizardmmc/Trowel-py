from pathlib import Path

import pytest
import yaml

import trowel_py.cc_host.service as service
from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
)
from trowel_py.cc_host.service import CCHost
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile
from trowel_py.schemas.cc_host import ErrorEvent, FinishedEvent


@pytest.fixture
def memory_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    return tmp_path


def _seed_profile(root: Path) -> None:
    MemoryStore(root).write_profile(
        Profile(ability="SPAWN_PROFILE_MARKER", updated="2026-07-14"),
        source="user-edit",
    )


def _seed_memory(root: Path) -> None:
    _seed_profile(root)
    frontmatter = {
        "type": "core",
        "items": [
            {
                "id": "core-1",
                "imperative": "SPAWN_CORE_MARKER",
                "scope": "high-risk",
                "status": "active",
                "source": "test",
            }
        ],
    }
    (root / "core.md").write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
        + "---\nbody\n",
        encoding="utf-8",
    )
    (root / "dictionary-L0.md").write_text(
        "SPAWN_L0_MARKER index",
        encoding="utf-8",
    )


def _make_host(
    spawner: FakeSpawner,
    workdir: Path,
    *,
    memory_enabled: bool = True,
    profile_enabled: bool = True,
    self_enabled: bool = True,
) -> CCHost:
    # 始终传入 MCP 路径，才能证明 memory-off 在宿主边界主动关闭读路径。
    return CCHost(
        "session-id",
        workdir,
        spawner=spawner,
        mcp_config=str(workdir / "memory-mcp.json"),
        memory_enabled=memory_enabled,
        profile_enabled=profile_enabled,
        self_enabled=self_enabled,
    )


def _prompt_of(args: list[str]) -> str | None:
    if "--append-system-prompt" not in args:
        return None
    return args[args.index("--append-system-prompt") + 1]


def _fail_injection(*args: object, **kwargs: object) -> str:
    raise RuntimeError("memory unavailable")


async def test_spawn_empty_memory_keeps_root_prompt(memory_root: Path) -> None:
    spawner = FakeSpawner([FakeProc([])])
    host = CCHost("session-id", memory_root, spawner=spawner)

    await host._spawn(None)

    prompt = _prompt_of(spawner.spawned[0][0])
    assert prompt is not None
    assert "# 用户画像" not in prompt
    assert "memory 根路径" in prompt


async def test_spawn_argv_memory_on_profile_on(memory_root: Path) -> None:
    _seed_memory(memory_root)
    spawner = FakeSpawner([FakeProc([])])
    host = _make_host(
        spawner,
        memory_root,
        memory_enabled=True,
        profile_enabled=True,
    )

    await host._spawn(None)

    args = spawner.spawned[0][0]
    prompt = _prompt_of(args)
    assert prompt is not None
    assert "SPAWN_CORE_MARKER" in prompt
    assert "SPAWN_L0_MARKER" in prompt
    assert "# 用户画像" in prompt
    assert "SPAWN_PROFILE_MARKER" in prompt
    assert "memory 根路径" in prompt
    assert "--mcp-config" in args


async def test_spawn_argv_memory_on_profile_off(memory_root: Path) -> None:
    _seed_memory(memory_root)
    spawner = FakeSpawner([FakeProc([])])
    host = _make_host(
        spawner,
        memory_root,
        memory_enabled=True,
        profile_enabled=False,
    )

    await host._spawn(None)

    args = spawner.spawned[0][0]
    prompt = _prompt_of(args)
    assert prompt is not None
    assert "SPAWN_CORE_MARKER" in prompt
    assert "SPAWN_L0_MARKER" in prompt
    assert "# 用户画像" not in prompt
    assert "SPAWN_PROFILE_MARKER" not in prompt
    assert "memory 根路径" in prompt
    assert "--mcp-config" in args


async def test_spawn_argv_memory_off_profile_on(memory_root: Path) -> None:
    _seed_memory(memory_root)
    spawner = FakeSpawner([FakeProc([])])
    host = _make_host(
        spawner,
        memory_root,
        memory_enabled=False,
        profile_enabled=True,
    )

    await host._spawn(None)

    args = spawner.spawned[0][0]
    prompt = _prompt_of(args)
    assert prompt is not None
    assert "# 用户画像" in prompt
    assert "SPAWN_CORE_MARKER" not in prompt
    assert "SPAWN_L0_MARKER" not in prompt
    assert "memory 根路径" not in prompt
    assert "memory.search" not in prompt
    assert "--mcp-config" not in args


async def test_spawn_argv_memory_off_profile_off(memory_root: Path) -> None:
    _seed_profile(memory_root)
    spawner = FakeSpawner([FakeProc([])])
    host = _make_host(
        spawner,
        memory_root,
        memory_enabled=False,
        profile_enabled=False,
        self_enabled=False,
    )

    await host._spawn(None)

    args = spawner.spawned[0][0]
    assert "--append-system-prompt" not in args
    assert "--mcp-config" not in args


async def test_spawn_includes_self_section_when_enabled(
    memory_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "build_memory_injection",
        lambda *args, **kwargs: "MEMORY_MARKER",
    )
    spawner = FakeSpawner([FakeProc([])])
    host = _make_host(spawner, memory_root)

    await host._spawn(None)

    prompt = _prompt_of(spawner.spawned[0][0])
    assert prompt is not None
    assert "# 关于你（Self" in prompt
    assert "Trowel" in prompt
    assert "持续主体" in prompt
    assert prompt.index("Trowel") < prompt.index("MEMORY_MARKER")


async def test_repeated_spawn_keeps_frozen_condition(memory_root: Path) -> None:
    _seed_profile(memory_root)
    spawner = FakeSpawner([FakeProc([]), FakeProc([])])
    host = _make_host(
        spawner,
        memory_root,
        memory_enabled=False,
        profile_enabled=True,
    )

    await host._spawn(None)
    await host._spawn(None)

    assert len(spawner.spawned) == 2
    assert spawner.spawned[0][0] == spawner.spawned[1][0]
    args = spawner.spawned[0][0]
    prompt = _prompt_of(args)
    assert prompt is not None
    assert "# 用户画像" in prompt
    assert "SPAWN_PROFILE_MARKER" in prompt
    assert "memory 根路径" not in prompt
    assert "--mcp-config" not in args


async def test_spawn_passes_memory_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "build_memory_injection",
        lambda now, **kwargs: "INJECTION_MARKER",
    )
    process = FakeProc([line(init_event()), line(result_ok())])
    spawner = FakeSpawner([process])
    host = CCHost("session-id", tmp_path, spawner=spawner, self_enabled=False)

    await collect(host.send("hi"))

    prompt = _prompt_of(spawner.spawned[0][0])
    assert prompt == "INJECTION_MARKER"
    assert "--mcp-config" not in spawner.spawned[0][0]
    assert "--strict-mcp-config" not in spawner.spawned[0][0]


async def test_spawn_survives_injection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trowel_py.cc_host.service as service

    monkeypatch.setattr(service, "build_memory_injection", _fail_injection)
    process = FakeProc([line(init_event()), line(result_ok())])
    spawner = FakeSpawner([process])
    host = CCHost("session-id", tmp_path, spawner=spawner, self_enabled=False)

    events = await collect(host.send("hi"))

    assert "--append-system-prompt" not in spawner.spawned[0][0]
    assert isinstance(events[-1], FinishedEvent)
    assert not any(isinstance(event, ErrorEvent) for event in events)


async def test_spawn_keeps_self_when_memory_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "build_memory_injection", _fail_injection)
    process = FakeProc([line(init_event()), line(result_ok())])
    spawner = FakeSpawner([process])
    host = CCHost("session-id", tmp_path, spawner=spawner)

    await collect(host.send("hi"))

    prompt = _prompt_of(spawner.spawned[0][0])
    assert prompt is not None
    assert "# 关于你（Self" in prompt
    assert "Trowel" in prompt
