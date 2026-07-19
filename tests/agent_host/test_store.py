"""slice-072: agent_host binding store — persistence + invariants.

Spec C-7: every test injects a tmp path; the autouse conftest fixture also
pins ``TROWEL_AGENT_SESSIONS_PATH`` to tmp as belt-and-braces, so the real
``~/.trowel/agent_sessions.json`` is never touched.
"""

from __future__ import annotations

import json

import pytest

from trowel_py.agent_host.binding import Runtime, SessionBinding, make_binding
from trowel_py.agent_host.store import BindingStore, resolve_bindings_path


def _binding(**over: object) -> SessionBinding:
    """Build a binding with sane defaults; tests override individual fields."""

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


def test_put_get_roundtrip(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    store.put(_binding())
    got = store.get("s1")
    assert got is not None
    assert got.session_id == "s1"
    assert got.runtime is Runtime.CLAUDE_CODE
    assert got.native_session_id is None
    assert got.capabilities == ("tools",)


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
    """Native facts learned from thread/start/cc init write back immutably."""

    store = BindingStore(tmp_path / "b.json")
    store.put(_binding())
    updated = store.update_native(
        "s1", native_session_id="cc-xyz", model="glm-5.2"
    )
    assert updated.native_session_id == "cc-xyz"
    assert updated.model == "glm-5.2"
    # persisted to disk — a fresh store sees the writeback
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
    """Spec pass criterion: binding recovers after a trowel restart."""

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


def test_missing_file_loads_empty_and_creates_parent_on_write(tmp_path):
    store = BindingStore(tmp_path / "nested" / "deep" / "b.json")
    assert store.list_all() == []
    store.put(_binding())
    assert store.get("s1") is not None


def test_atomic_write_leaves_valid_json_no_tmp_fragment(tmp_path):
    """Spec: binding migration must be safe; write is tmp + rename, no half file."""

    path = tmp_path / "b.json"
    store = BindingStore(path)
    store.put(_binding(native_session_id="cc-1"))
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "s1" in data["sessions"]
    assert data["sessions"]["s1"]["native_session_id"] == "cc-1"
    assert not list(tmp_path.glob("*.tmp"))


def test_resolve_bindings_path_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom-location.json"
    monkeypatch.setenv("TROWEL_AGENT_SESSIONS_PATH", str(custom))
    assert resolve_bindings_path() == custom


def test_runtime_enum_wire_values():
    assert Runtime.CLAUDE_CODE.value == "claude_code"
    assert Runtime.CODEX.value == "codex"


def test_binding_is_immutable(tmp_path):
    """coding-style: frozen dataclass — mutation raises."""

    b = _binding()
    with pytest.raises(Exception):
        b.runtime = Runtime.CODEX  # type: ignore[misc]
