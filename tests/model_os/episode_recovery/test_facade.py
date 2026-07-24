from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from tests.model_os._episode_helpers import make_cooperative_snapshot
from tests.model_os.episode_recovery.support import build, build_direct
from trowel_py.model_os import store as store_module
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import SnapshotSource


def test_build_recovery_partial_is_staticmethod() -> None:
    attr = ModelOsStore.__dict__.get("build_recovery_partial")
    assert isinstance(attr, staticmethod)
    assert ModelOsStore.build_recovery_partial.__module__ == store_module.__name__


def test_build_recovery_partial_signature_has_no_store_or_model_args() -> None:
    signature = inspect.signature(ModelOsStore.build_recovery_partial)
    assert str(signature) == (
        "(*, work_item_goal: 'str', task_constraints_ref: 'str | None', "
        "prev: 'EpisodeSnapshot | None', journal_through_seq: 'int', "
        "prev_ref: 'SnapshotRef | None' = None, "
        "events: 'tuple[EventEnvelope, ...]' = ()) -> 'EpisodeSnapshot'"
    )
    assert tuple(signature.parameters) == (
        "work_item_goal",
        "task_constraints_ref",
        "prev",
        "journal_through_seq",
        "prev_ref",
        "events",
    )
    forbidden = ("store", "conn", "db", "session", "client", "model", "api")
    assert all(
        not any(part in name.lower() for part in forbidden)
        for name in signature.parameters
    )


def test_build_recovery_partial_does_not_need_an_open_store() -> None:
    snapshot = build(journal_through_seq=42)
    assert snapshot.source == SnapshotSource.RECOVERY_PARTIAL
    assert snapshot.journal_through_seq == 42


def test_facade_matches_direct_builder() -> None:
    prev = make_cooperative_snapshot()
    kwargs = {
        "work_item_goal": prev.work_item_goal,
        "task_constraints_ref": "task-1",
        "prev": prev,
        "journal_through_seq": 9,
    }
    assert build(**kwargs) == build_direct(**kwargs)


def test_facade_injects_store_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_snapshot = object()
    sentinel_side_effect = object()
    sentinel_source = SimpleNamespace(RECOVERY_PARTIAL=object())
    sentinel_kind = SimpleNamespace(EPISODE_SIDE_EFFECT_RECORDED=object())
    observed = {}

    def run(**kwargs):
        observed.update(kwargs)
        return "result"

    monkeypatch.setattr(store_module, "EpisodeSnapshot", sentinel_snapshot)
    monkeypatch.setattr(store_module, "SideEffectRecord", sentinel_side_effect)
    monkeypatch.setattr(store_module, "SnapshotSource", sentinel_source)
    monkeypatch.setattr(store_module, "EventKind", sentinel_kind)
    monkeypatch.setattr(store_module, "_run_build_recovery_partial", run)

    assert build() == "result"
    assert observed["snapshot_type"] is sentinel_snapshot
    assert observed["side_effect_type"] is sentinel_side_effect
    assert observed["recovery_source"] is sentinel_source.RECOVERY_PARTIAL
    assert (
        observed["side_effect_event_kind"] is sentinel_kind.EPISODE_SIDE_EFFECT_RECORDED
    )
