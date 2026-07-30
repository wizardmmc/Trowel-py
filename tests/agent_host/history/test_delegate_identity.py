from __future__ import annotations

import fcntl
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.delegate_identity import (
    DelegateIdentityIndexError,
    DelegateIdentityStore,
    delegate_identity_path,
)
from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.store import BindingStore


def _add_codex_identity(path: str, ready: Any, done: Any) -> None:
    """在独立进程中登记一个 Codex 委派身份。"""

    ready.set()
    DelegateIdentityStore(Path(path)).add(Runtime.CODEX, "codex-from-child")
    done.set()


def _read_cc_identities(path: str, ready: Any, done: Any) -> None:
    """在独立进程中读取 Claude Code 委派身份。"""

    ready.set()
    DelegateIdentityStore(Path(path)).ids(Runtime.CLAUDE_CODE)
    done.set()


def test_identity_survives_restart_and_keeps_only_runtime_ids(tmp_path: Path) -> None:
    path = tmp_path / "agent_sessions.delegates.json"
    store = DelegateIdentityStore(path)

    store.add(Runtime.CLAUDE_CODE, "cc-native-1")
    store.add(Runtime.CODEX, "codex-native-1")
    store.add(Runtime.CODEX, "codex-native-1")

    restarted = DelegateIdentityStore(path)
    assert restarted.ids(Runtime.CLAUDE_CODE) == frozenset({"cc-native-1"})
    assert restarted.ids(Runtime.CODEX) == frozenset({"codex-native-1"})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "native_session_ids": {
            "claude_code": ["cc-native-1"],
            "codex": ["codex-native-1"],
        },
    }
    assert not list(tmp_path.glob("*.tmp"))


def test_writer_waits_for_other_process_read_modify_write_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agent_sessions.delegates.json"
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    done = context.Event()
    process = context.Process(
        target=_add_codex_identity,
        args=(str(path), ready, done),
    )

    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        process.start()
        assert ready.wait(timeout=5)
        assert done.wait(timeout=0.2) is False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert done.wait(timeout=5)
    process.join(timeout=5)
    assert process.exitcode == 0
    assert DelegateIdentityStore(path).ids(Runtime.CODEX) == frozenset(
        {"codex-from-child"}
    )


def test_reader_waits_until_identity_write_cycle_finishes(tmp_path: Path) -> None:
    path = tmp_path / "agent_sessions.delegates.json"
    store = DelegateIdentityStore(path)
    store.add(Runtime.CLAUDE_CODE, "cc-native")
    lock_path = path.with_name(path.name + ".lock")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    done = context.Event()
    process = context.Process(
        target=_read_cc_identities,
        args=(str(path), ready, done),
    )

    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        process.start()
        assert ready.wait(timeout=5)
        assert done.wait(timeout=0.2) is False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert done.wait(timeout=5)
    process.join(timeout=5)
    assert process.exitcode == 0


def test_identity_path_is_separate_from_binding_file(tmp_path: Path) -> None:
    bindings_path = tmp_path / "agent_sessions.json"

    assert delegate_identity_path(bindings_path) == (
        tmp_path / "agent_sessions.delegates.json"
    )


def test_invalid_index_does_not_silently_expose_delegate_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "delegate.json"
    path.write_text('{"version": 99}', encoding="utf-8")

    with pytest.raises(DelegateIdentityIndexError, match="invalid schema"):
        DelegateIdentityStore(path).ids(Runtime.CLAUDE_CODE)


def test_hub_migrates_only_known_delegate_bindings(tmp_path: Path) -> None:
    bindings_path = tmp_path / "agent_sessions.json"
    store = BindingStore(bindings_path)
    common = {
        "runtime": Runtime.CLAUDE_CODE,
        "workdir": "/workspace",
        "model": None,
        "effort": None,
        "permission": None,
        "memory_enabled": True,
        "profile_enabled": True,
        "capabilities": ("tools",),
        "name": "workspace",
    }
    store.put(
        make_binding(
            session_id="delegate-binding",
            native_session_id="delegate-native",
            session_kind="delegate",
            **common,
        )
    )
    store.put(
        make_binding(
            session_id="user-binding",
            native_session_id="user-native",
            **common,
        )
    )

    SessionHub(store, cc_registry={}, cc_opener=lambda *_args, **_kwargs: None)

    identities = DelegateIdentityStore(delegate_identity_path(bindings_path))
    assert identities.ids(Runtime.CLAUDE_CODE) == frozenset({"delegate-native"})
