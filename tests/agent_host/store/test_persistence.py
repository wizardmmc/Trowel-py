from __future__ import annotations

import fcntl
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from trowel_py.agent_host.binding import Runtime, SessionBinding, make_binding
from trowel_py.agent_host.capabilities import (
    CURRENT_CAPABILITY_VERSION,
    capabilities_for_runtime,
)
from trowel_py.agent_host.store import (
    BindingStore,
    next_session_display_name,
    resolve_bindings_path,
)


def _binding(**over: object) -> SessionBinding:
    base: dict[str, object] = dict(
        session_id="s1",
        runtime=Runtime.CLAUDE_CODE,
        native_session_id=None,
        workdir="/tmp/proj",
        model=None,
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools",),
        name="proj",
    )
    base.update(over)
    return make_binding(**base)  # type: ignore[arg-type]


def _put_binding(path: str, ready: Any, done: Any) -> None:
    """在独立进程中写入一个 binding。"""

    ready.set()
    BindingStore(Path(path)).put(_binding(session_id="child"))
    done.set()


@pytest.mark.parametrize(
    ("occupied_names", "expected"),
    [
        ([], "proj"),
        (["proj"], "proj #2"),
        (["proj", "proj #3"], "proj #2"),
        (["proj #2", "proj #3"], "proj"),
        (["手动标题", "proj #x", "proj #1", "proj #²"], "proj"),
    ],
)
def test_next_session_display_name_uses_smallest_available_ordinal(
    occupied_names: list[str],
    expected: str,
) -> None:
    assert next_session_display_name("/tmp/proj", occupied_names) == expected


def test_put_get_roundtrip(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    store.put(
        _binding(
            display_title="排查会话标题",
            title_source="generated",
        )
    )
    got = store.get("s1")
    assert got is not None
    assert got.session_id == "s1"
    assert got.runtime is Runtime.CLAUDE_CODE
    assert got.native_session_id is None
    assert got.capabilities == ("tools",)
    assert got.display_title == "排查会话标题"
    assert got.title_source == "generated"


def test_old_binding_defaults_to_new_title_state(tmp_path):
    path = tmp_path / "b.json"
    binding = _binding().to_dict()
    del binding["display_title"]
    del binding["title_source"]
    path.write_text(
        json.dumps({"version": 1, "sessions": {"s1": binding}}),
        encoding="utf-8",
    )

    got = BindingStore(path).get("s1")

    assert got is not None
    assert got.display_title == ""
    assert got.title_source == "new"


@pytest.mark.parametrize("runtime", [Runtime.CLAUDE_CODE, Runtime.CODEX])
@pytest.mark.parametrize("invalid_version", [None, 0, -1, True, "1"])
def test_old_binding_upgrades_missing_or_invalid_capability_roster(
    tmp_path: Path,
    runtime: Runtime,
    invalid_version: object,
) -> None:
    """旧 binding 没有合法版本号时按当前矩阵补齐，避免恢复后误隐藏功能。"""

    path = tmp_path / "b.json"
    binding = _binding(runtime=runtime, capabilities=("tools",)).to_dict()
    if invalid_version is None:
        del binding["capability_version"]
    else:
        binding["capability_version"] = invalid_version
    path.write_text(
        json.dumps({"version": 1, "sessions": {"s1": binding}}),
        encoding="utf-8",
    )

    got = BindingStore(path).get("s1")

    assert got is not None
    assert got.capability_version == CURRENT_CAPABILITY_VERSION
    assert got.capabilities == capabilities_for_runtime(runtime.value)


def test_future_capability_roster_is_preserved_for_older_reader(tmp_path: Path) -> None:
    """较新程序写出的合法版本不能被旧程序按自己的矩阵静默降级。"""

    path = tmp_path / "b.json"
    binding = _binding(capabilities=("tools", "future-capability")).to_dict()
    binding["capability_version"] = CURRENT_CAPABILITY_VERSION + 1
    path.write_text(
        json.dumps({"version": 1, "sessions": {"s1": binding}}),
        encoding="utf-8",
    )

    got = BindingStore(path).get("s1")

    assert got is not None
    assert got.capability_version == CURRENT_CAPABILITY_VERSION + 1
    assert got.capabilities == ("tools", "future-capability")


def test_put_overwrite_updates_fields(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    store.put(_binding())
    store.put(_binding(native_session_id="cc-1", model="glm-5.2"))
    got = store.get("s1")
    assert got is not None
    assert got.native_session_id == "cc-1"
    assert got.model == "glm-5.2"


def test_list_all_returns_every_binding(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    store.put(_binding(session_id="s1"))
    store.put(_binding(session_id="s2", runtime=Runtime.CODEX))
    ids = {b.session_id for b in store.list_all()}
    assert ids == {"s1", "s2"}


def test_delete_removes_binding(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    store.put(_binding())
    assert store.delete("s1") is True
    assert store.get("s1") is None
    assert store.delete("s1") is False


def test_update_native_atomic_writeback(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    store.put(_binding())
    updated = store.update_native("s1", native_session_id="cc-xyz", model="glm-5.2")
    assert updated.native_session_id == "cc-xyz"
    assert updated.model == "glm-5.2"
    restarted = BindingStore(tmp_path / "b.json")
    got = restarted.get("s1")
    assert got is not None
    assert got.native_session_id == "cc-xyz"
    assert got.model == "glm-5.2"


def test_update_native_unknown_session_raises(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    with pytest.raises(KeyError):
        store.update_native("nope", native_session_id="x")


def test_persistence_survives_new_store_instance(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    store.put(_binding(session_id="s1", native_session_id="cc-1"))
    restarted = BindingStore(tmp_path / "b.json")
    got = restarted.get("s1")
    assert got is not None
    assert got.native_session_id == "cc-1"
    assert got.runtime is Runtime.CLAUDE_CODE


def test_empty_store_returns_empty(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    assert store.list_all() == []
    assert store.get("nope") is None


@pytest.mark.parametrize("payload", [[], None, "not-a-mapping"])
def test_non_mapping_root_loads_empty(tmp_path, payload):
    path = tmp_path / "b.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    store = BindingStore(path)

    assert store.list_all() == []
    assert store.get("nope") is None


@pytest.mark.parametrize("invalid_roster", [42, "tools", {"name": "tools"}])
def test_invalid_declared_mcp_roster_loads_empty(tmp_path, invalid_roster):
    path = tmp_path / "b.json"
    binding = _binding().to_dict()
    binding["declared_mcp_roster"] = invalid_roster
    path.write_text(
        json.dumps({"version": 1, "sessions": {"s1": binding}}),
        encoding="utf-8",
    )

    got = BindingStore(path).get("s1")

    assert got is not None
    assert got.declared_mcp_roster == ()


def test_missing_required_binding_field_still_raises(tmp_path):
    path = tmp_path / "b.json"
    binding = _binding().to_dict()
    del binding["name"]
    path.write_text(
        json.dumps({"version": 1, "sessions": {"s1": binding}}),
        encoding="utf-8",
    )

    with pytest.raises(KeyError):
        BindingStore(path).get("s1")


def test_unknown_runtime_still_raises(tmp_path):
    path = tmp_path / "b.json"
    binding = _binding().to_dict()
    binding["runtime"] = "unknown"
    path.write_text(
        json.dumps({"version": 1, "sessions": {"s1": binding}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        BindingStore(path).get("s1")


def test_missing_file_loads_empty_and_creates_parent_on_write(tmp_path):
    store = BindingStore(tmp_path / "nested" / "deep" / "b.json")
    assert store.list_all() == []
    store.put(_binding())
    assert store.get("s1") is not None


def test_atomic_write_leaves_valid_json_no_tmp_fragment(tmp_path):
    path = tmp_path / "b.json"
    store = BindingStore(path)
    store.put(_binding(native_session_id="cc-1"))
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "s1" in data["sessions"]
    assert data["sessions"]["s1"]["native_session_id"] == "cc-1"
    assert not list(tmp_path.glob("*.tmp"))


def test_binding_writer_waits_for_cross_process_read_modify_write_lock(tmp_path):
    path = tmp_path / "b.json"
    lock_path = path.with_name(path.name + ".lock")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    done = context.Event()
    process = context.Process(
        target=_put_binding,
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
    assert BindingStore(path).get("child") is not None


def test_resolve_bindings_path_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom-location.json"
    monkeypatch.setenv("TROWEL_AGENT_SESSIONS_PATH", str(custom))
    assert resolve_bindings_path() == custom


def test_runtime_enum_wire_values():
    assert Runtime.CLAUDE_CODE.value == "claude_code"
    assert Runtime.CODEX.value == "codex"


def test_binding_is_immutable(tmp_path):
    b = _binding()
    with pytest.raises(Exception):
        b.runtime = Runtime.CODEX  # type: ignore[misc]
