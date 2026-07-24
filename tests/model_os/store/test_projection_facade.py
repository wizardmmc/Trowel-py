from __future__ import annotations

import inspect
import typing
from types import SimpleNamespace
from typing import Any

import pytest

from trowel_py.model_os import store as store_module
from trowel_py.model_os.reducer import EpisodeState, TaskState
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import Episode, Task


def _task_state() -> SimpleNamespace:
    return SimpleNamespace(
        task_id=object(),
        origin=object(),
        original_goal=object(),
        appended_constraints=object(),
        status=object(),
        status_provenance=object(),
        priority=object(),
        warm=object(),
        warm_rank=object(),
        authorization_scope=object(),
        waiting_condition=object(),
        completion_evidence=object(),
        error_record=object(),
        primary_work_item_id=object(),
        created_at=object(),
        updated_at=object(),
    )


def _episode_state() -> SimpleNamespace:
    return SimpleNamespace(
        episode_id=object(),
        work_item_id=object(),
        task_id=object(),
        status=object(),
        status_provenance=object(),
        native_session_id=object(),
        pending_descriptor=object(),
        reconcile_reason=object(),
        last_snapshot_ref=object(),
        created_at=object(),
        updated_at=object(),
    )


def test_projection_facades_keep_complete_contracts() -> None:
    task_descriptor = ModelOsStore.__dict__["_task_state_to_task"]
    task = ModelOsStore._task_state_to_task
    episode = ModelOsStore._episode_state_to_episode

    assert isinstance(task_descriptor, staticmethod)
    assert str(inspect.signature(task)) == "(state: 'TaskState') -> 'Task'"
    assert str(inspect.signature(episode)) == (
        "(self, state: 'EpisodeState', ownership_lease_id: 'str | None' = None) "
        "-> 'Episode'"
    )
    for name, facade in (
        ("_task_state_to_task", task),
        ("_episode_state_to_episode", episode),
    ):
        assert facade.__module__ == store_module.__name__
        assert facade.__qualname__ == f"ModelOsStore.{name}"
    assert typing.get_type_hints(task) == {
        "state": TaskState,
        "return": Task,
    }
    assert typing.get_type_hints(episode) == {
        "state": EpisodeState,
        "ownership_lease_id": str | None,
        "return": Episode,
    }


def test_projection_facades_preserve_complete_legacy_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_state = _task_state()
    episode_state = _episode_state()
    task_kwargs: dict[str, Any] = {}
    episode_kwargs: dict[str, Any] = {}

    def task_type(**kwargs: Any) -> object:
        task_kwargs.update(kwargs)
        return task_kwargs

    def episode_type(**kwargs: Any) -> object:
        episode_kwargs.update(kwargs)
        return episode_kwargs

    monkeypatch.setattr(store_module, "Task", task_type)
    monkeypatch.setattr(store_module, "Episode", episode_type)
    store = ModelOsStore(":memory:")
    ownership_lease_id = object()

    task = store._task_state_to_task(task_state)  # type: ignore[arg-type]
    episode = store._episode_state_to_episode(  # type: ignore[arg-type]
        episode_state,
        ownership_lease_id,  # type: ignore[arg-type]
    )

    assert task is task_kwargs
    assert list(task_kwargs) == [
        "task_id",
        "origin",
        "original_goal",
        "appended_constraints",
        "status",
        "priority",
        "warm",
        "warm_rank",
        "authorization_scope",
        "waiting_condition",
        "completion_evidence",
        "error_record",
        "primary_work_item_id",
        "created_at",
        "updated_at",
    ]
    assert task_kwargs == {
        "task_id": task_state.task_id,
        "origin": task_state.origin,
        "original_goal": task_state.original_goal,
        "appended_constraints": task_state.appended_constraints,
        "status": task_state.status,
        "priority": task_state.priority,
        "warm": task_state.warm,
        "warm_rank": task_state.warm_rank,
        "authorization_scope": task_state.authorization_scope,
        "waiting_condition": task_state.waiting_condition,
        "completion_evidence": task_state.completion_evidence,
        "error_record": task_state.error_record,
        "primary_work_item_id": task_state.primary_work_item_id,
        "created_at": task_state.created_at,
        "updated_at": task_state.updated_at,
    }
    assert episode is episode_kwargs
    assert list(episode_kwargs) == [
        "episode_id",
        "work_item_id",
        "task_id",
        "status",
        "native_session_id",
        "ownership_lease_id",
        "last_snapshot_ref",
        "pending_descriptor",
        "reconcile_reason",
        "created_at",
        "updated_at",
    ]
    assert episode_kwargs == {
        "episode_id": episode_state.episode_id,
        "work_item_id": episode_state.work_item_id,
        "task_id": episode_state.task_id,
        "status": episode_state.status,
        "native_session_id": episode_state.native_session_id,
        "ownership_lease_id": ownership_lease_id,
        "last_snapshot_ref": episode_state.last_snapshot_ref,
        "pending_descriptor": episode_state.pending_descriptor,
        "reconcile_reason": episode_state.reconcile_reason,
        "created_at": episode_state.created_at,
        "updated_at": episode_state.updated_at,
    }
    assert task_state.status_provenance not in task_kwargs.values()
    assert episode_state.status_provenance not in episode_kwargs.values()


@pytest.mark.parametrize("projection", ["task", "episode"])
def test_projection_facades_capture_store_constructor_before_state_reads(
    monkeypatch: pytest.MonkeyPatch,
    projection: str,
) -> None:
    calls: list[str] = []

    def original_type(**kwargs: Any) -> object:
        calls.append("original")
        return kwargs

    def replacement_type(**kwargs: Any) -> object:
        calls.append("replacement")
        return kwargs

    class MutatingState:
        def __getattribute__(self, name: str) -> object:
            if name in {"__dict__", "__class__"}:
                return object.__getattribute__(self, name)
            calls.append(name)
            target = "Task" if projection == "task" else "Episode"
            monkeypatch.setattr(store_module, target, replacement_type)
            return object()

    target = "Task" if projection == "task" else "Episode"
    monkeypatch.setattr(store_module, target, original_type)
    store = ModelOsStore(":memory:")

    if projection == "task":
        store._task_state_to_task(MutatingState())  # type: ignore[arg-type]
        assert calls[:-1] == [
            "task_id",
            "origin",
            "original_goal",
            "appended_constraints",
            "status",
            "priority",
            "warm",
            "warm_rank",
            "authorization_scope",
            "waiting_condition",
            "completion_evidence",
            "error_record",
            "primary_work_item_id",
            "created_at",
            "updated_at",
        ]
    else:
        store._episode_state_to_episode(MutatingState())  # type: ignore[arg-type]
        assert calls[:-1] == [
            "episode_id",
            "work_item_id",
            "task_id",
            "status",
            "native_session_id",
            "last_snapshot_ref",
            "pending_descriptor",
            "reconcile_reason",
            "created_at",
            "updated_at",
        ]
    assert calls[-1] == "original"


def test_projection_facades_delegate_current_store_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    task_result = object()
    episode_result = object()

    def project_task(*args: Any, **kwargs: Any) -> object:
        observed["task"] = (args, kwargs)
        return task_result

    def project_episode(*args: Any, **kwargs: Any) -> object:
        observed["episode"] = (args, kwargs)
        return episode_result

    monkeypatch.setattr(
        store_module,
        "_run_project_task_state",
        project_task,
        raising=False,
    )
    monkeypatch.setattr(
        store_module,
        "_run_project_episode_state",
        project_episode,
        raising=False,
    )
    task_type = object()
    episode_type = object()
    monkeypatch.setattr(store_module, "Task", task_type)
    monkeypatch.setattr(store_module, "Episode", episode_type)
    task_state = object()
    episode_state = object()
    store = ModelOsStore(":memory:")

    assert store._task_state_to_task(task_state) is task_result  # type: ignore[arg-type]
    assert (
        store._episode_state_to_episode(  # type: ignore[arg-type]
            episode_state,
            "lease-1",
        )
        is episode_result
    )
    assert observed == {
        "task": ((task_state,), {"task_type": task_type}),
        "episode": (
            (episode_state, "lease-1"),
            {"episode_type": episode_type},
        ),
    }
