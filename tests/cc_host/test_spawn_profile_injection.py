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
