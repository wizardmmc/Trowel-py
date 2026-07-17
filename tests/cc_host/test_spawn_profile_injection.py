"""slice-048 integration: profile reaches cc spawn's --append-system-prompt.

The unit tests in tests/memory/test_injection.py prove build_memory_injection
emits the profile section. This test proves the WIRING: that section flows
through service._spawn → build_args → ``--append-system-prompt`` with no real
cc (a capturing spawner). This matches milestone-7 验收标准 "注入生效：cc spawn
时 --append-system-prompt 带 profile 段（有测试）".

Memory-root redirect: build_memory_injection calls ``resolve_memory_root()``
via a name it bound at import (``from ... import resolve_memory_root``). So we
patch ``trowel_py.memory.injection.resolve_memory_root`` — patching
``paths.resolve_memory_root`` (what tests/cc_host/test_session_registration.py
does) does NOT reach injection's own binding.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trowel_py.cc_host.service import CCHost
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile


class _CapturingSpawner:
    """Record the argv list _spawn builds; return no real subprocess."""

    def __init__(self) -> None:
        self.captured_args: list[str] | None = None

    async def __call__(self, args: list[str], kwargs: dict) -> None:
        self.captured_args = list(args)
        return None


class _MultiCaptureSpawner:
    """Record EVERY argv list across multiple _spawn calls (respawn tests)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, args: list[str], kwargs: dict) -> None:
        self.calls.append(list(args))
        return None


async def test_spawn_args_carry_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    MemoryStore(tmp_path).write_profile(
        Profile(ability="SPAWN_PROFILE_MARKER", updated="2026-07-14"),
        source="user-edit",
    )

    spawner = _CapturingSpawner()
    host = CCHost("trowel-sid", tmp_path, spawner=spawner)
    await host._spawn(None)

    assert spawner.captured_args is not None
    args = spawner.captured_args
    assert "--append-system-prompt" in args
    prompt = args[args.index("--append-system-prompt") + 1]
    assert "# 用户画像" in prompt
    assert "SPAWN_PROFILE_MARKER" in prompt


async def test_spawn_args_omit_append_prompt_when_no_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # C-4 end-to-end: empty memory (no profile, no core/L0/diary) → injection is
    # just the memory-root section, still non-empty, so the flag is still added.
    # This test pins that an absent profile does not break the spawn wiring.
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )

    spawner = _CapturingSpawner()
    host = CCHost("trowel-sid", tmp_path, spawner=spawner)
    await host._spawn(None)

    assert spawner.captured_args is not None
    args = spawner.captured_args
    # memory-root section is always injected → flag present, just no profile heading
    assert "--append-system-prompt" in args
    prompt = args[args.index("--append-system-prompt") + 1]
    assert "# 用户画像" not in prompt


# ---------- slice-060: memory/profile A/B switches at the spawn argv level ----------

_MCP_CONFIG_PATH = "/tmp/test-mcp-config.json"


def _seed_profile(root: Path) -> None:
    """Seed one non-empty profile so the profile section is renderable."""
    MemoryStore(root).write_profile(
        Profile(ability="SPAWN_PROFILE_MARKER", updated="2026-07-14"),
        source="user-edit",
    )


def _seed_memory(root: Path) -> None:
    """Seed core + profile + L0 so every section has a marker to assert on.

    Used by the memory-on spawn test to pin the contract that _spawn injects
    core/L0 (not just the always-on memory-root) — closing the gap between the
    injection-layer tests and the spawn wiring.
    """
    _seed_profile(root)
    core_fm = {
        "type": "core",
        "items": [
            {
                "id": "c1",
                "imperative": "SPAWN_CORE_MARKER",
                "scope": "high-risk",
                "status": "active",
                "source": "test",
            }
        ],
    }
    (root / "core.md").write_text(
        "---\n"
        + yaml.safe_dump(core_fm, sort_keys=False, allow_unicode=True)
        + "---\nbody\n",
        encoding="utf-8",
    )
    (root / "dictionary-L0.md").write_text("SPAWN_L0_MARKER index", encoding="utf-8")


def _make_host(
    spawner: _CapturingSpawner | _MultiCaptureSpawner,
    tmp_path: Path,
    *,
    memory_enabled: bool = True,
    profile_enabled: bool = True,
) -> CCHost:
    """Build a CCHost wired to the capturing spawner + an explicit mcp path.

    mcp_config is ALWAYS passed here so the memory-off cases prove CCHost
    detaches it on its own (C-3), independent of whatever routes does.
    """
    return CCHost(
        "trowel-sid",
        tmp_path,
        spawner=spawner,
        mcp_config=_MCP_CONFIG_PATH,
        memory_enabled=memory_enabled,
        profile_enabled=profile_enabled,
    )


def _prompt_of(args: list[str]) -> str | None:
    """Return the --append-system-prompt value, or None when the flag is absent."""
    if "--append-system-prompt" not in args:
        return None
    return args[args.index("--append-system-prompt") + 1]


async def test_spawn_argv_memory_on_profile_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # production default: profile section + memory read-path + MCP all present.
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    _seed_memory(tmp_path)
    spawner = _CapturingSpawner()
    host = _make_host(spawner, tmp_path, memory_enabled=True, profile_enabled=True)
    await host._spawn(None)

    args = spawner.captured_args
    assert args is not None
    prompt = _prompt_of(args)
    assert prompt is not None
    # core + L0 markers prove _spawn injects the full memory body (not just the
    # always-on memory-root) — pins the spawn↔injection contract.
    assert "SPAWN_CORE_MARKER" in prompt
    assert "SPAWN_L0_MARKER" in prompt
    assert "# 用户画像" in prompt
    assert "memory 根路径" in prompt
    assert "--mcp-config" in args


async def test_spawn_argv_memory_on_profile_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # profile off: memory read-path + MCP stay; only the profile heading is gone.
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    _seed_memory(tmp_path)
    spawner = _CapturingSpawner()
    host = _make_host(spawner, tmp_path, memory_enabled=True, profile_enabled=False)
    await host._spawn(None)

    args = spawner.captured_args
    prompt = _prompt_of(args)
    assert prompt is not None
    assert "SPAWN_CORE_MARKER" in prompt  # memory still fully injected
    assert "SPAWN_L0_MARKER" in prompt
    assert "# 用户画像" not in prompt
    assert "SPAWN_PROFILE_MARKER" not in prompt
    assert "memory 根路径" in prompt
    assert "--mcp-config" in args


async def test_spawn_argv_memory_off_profile_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # memory off, profile on: profile-only prompt, NO memory root, NO --mcp-config.
    # C-3: mcp_config WAS passed to the constructor but memory_enabled=False
    # detaches it — CCHost freezes this at construction, not at the route layer.
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    _seed_memory(tmp_path)  # core/L0 seeded but must NOT appear (memory off)
    spawner = _CapturingSpawner()
    host = _make_host(spawner, tmp_path, memory_enabled=False, profile_enabled=True)
    await host._spawn(None)

    args = spawner.captured_args
    prompt = _prompt_of(args)
    assert prompt is not None
    assert "# 用户画像" in prompt
    assert "SPAWN_CORE_MARKER" not in prompt  # memory sections all dropped
    assert "SPAWN_L0_MARKER" not in prompt
    assert "memory 根路径" not in prompt
    assert "memory.search" not in prompt
    assert "--mcp-config" not in args


async def test_spawn_argv_memory_off_profile_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # both off: no --append-system-prompt at all, no --mcp-config. Clean baseline.
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    _seed_profile(tmp_path)
    spawner = _CapturingSpawner()
    host = _make_host(spawner, tmp_path, memory_enabled=False, profile_enabled=False)
    await host._spawn(None)

    args = spawner.captured_args
    assert "--append-system-prompt" not in args
    assert "--mcp-config" not in args


async def test_respawn_keeps_frozen_condition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # C-5: a second _spawn (the respawn path after /model, stall, interrupt)
    # rebuilds the IDENTICAL argv. The frozen switches must not drift mid-session.
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    _seed_profile(tmp_path)
    spawner = _MultiCaptureSpawner()
    host = _make_host(spawner, tmp_path, memory_enabled=False, profile_enabled=True)
    await host._spawn(None)
    await host._spawn(None)  # simulate respawn

    assert len(spawner.calls) == 2
    assert spawner.calls[0] == spawner.calls[1]
    # and the frozen condition holds across both: profile-only, no MCP
    assert "--mcp-config" not in spawner.calls[0]
    assert _prompt_of(spawner.calls[0]) is not None
