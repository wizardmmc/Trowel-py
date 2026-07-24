from __future__ import annotations

import inspect
import typing
from types import SimpleNamespace
from typing import Any

import pytest

from trowel_py.model_os import store as store_module
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    Provenance,
    WorkItemStatus,
)


def _store() -> ModelOsStore:
    return ModelOsStore(":memory:", policy_version="policy-test")


def test_event_factory_facades_keep_complete_contracts() -> None:
    task = ModelOsStore._make_task_event
    work_item = ModelOsStore._work_item_status_event
    episode = ModelOsStore._make_episode_event

    assert str(inspect.signature(task)) == (
        "(self, kind: 'str', task_id: 'str', payload: 'dict[str, Any]', "
        "provenance: 'Provenance' = <Provenance.MACHINE_OBSERVATION: "
        "'machine_observation'>, work_item_id: 'str | None' = None) -> "
        "'EventEnvelope'"
    )
    assert str(inspect.signature(work_item)) == (
        "(self, work_item_id: 'str', new_status: 'WorkItemStatus', "
        "task_id: 'str | None', now: 'str') -> 'EventEnvelope'"
    )
    assert str(inspect.signature(episode)) == (
        "(self, kind: 'str', episode_id: 'str', payload: 'dict[str, Any]', *, "
        "work_item_id: 'str | None' = None, task_id: 'str | None' = None, "
        "provenance: 'Provenance' = <Provenance.MACHINE_OBSERVATION: "
        "'machine_observation'>, lease_id: 'str | None' = None, "
        "owner: 'str | None' = None, fencing_token: 'int | None' = None, "
        "event_id: 'str | None' = None) -> 'EventEnvelope'"
    )
    for name, facade in (
        ("_make_task_event", task),
        ("_work_item_status_event", work_item),
        ("_make_episode_event", episode),
    ):
        assert facade.__module__ == store_module.__name__
        assert facade.__qualname__ == f"ModelOsStore.{name}"
        typing.get_type_hints(facade)


def test_event_factory_facades_preserve_legacy_shapes_and_call_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    def uuid4() -> SimpleNamespace:
        calls.append(("uuid4", None))
        return SimpleNamespace(hex="uuid-test")

    def now_iso() -> str:
        calls.append(("now", None))
        return "2026-07-24T01:02:03+00:00"

    def event_type(**kwargs: Any) -> dict[str, Any]:
        calls.append(("event", kwargs))
        return kwargs

    monkeypatch.setattr(store_module, "uuid4", uuid4)
    monkeypatch.setattr(store_module, "_now_iso", now_iso)
    monkeypatch.setattr(store_module, "EventEnvelope", event_type)
    store = _store()

    task_payload = {"new_status": "ready"}
    task_event = store._make_task_event(
        EventKind.TASK_STATUS_CHANGED,
        "task-1",
        task_payload,
        Provenance.USER_DECISION,
        "work-1",
    )
    assert calls == [
        ("uuid4", None),
        ("now", None),
        (
            "event",
            {
                "event_id": "task.status_changed.uuid-test",
                "kind": EventKind.TASK_STATUS_CHANGED,
                "occurred_at": "2026-07-24T01:02:03+00:00",
                "source": "kernel",
                "provenance": Provenance.USER_DECISION,
                "policy_version": "policy-test",
                "payload": task_payload,
                "task_id": "task-1",
                "work_item_id": "work-1",
            },
        ),
    ]
    assert task_event is calls[-1][1]

    calls.clear()
    work_item_event = store._work_item_status_event(
        "work-1",
        WorkItemStatus.RUNNING,
        "task-1",
        "2026-07-24T04:05:06+00:00",
    )
    assert calls == [
        ("uuid4", None),
        (
            "event",
            {
                "event_id": "wi.status.work-1.uuid-test",
                "kind": EventKind.WORK_ITEM_STATUS_CHANGED,
                "occurred_at": "2026-07-24T04:05:06+00:00",
                "source": "kernel",
                "provenance": Provenance.MACHINE_OBSERVATION,
                "policy_version": "policy-test",
                "payload": {"new_status": "running"},
                "work_item_id": "work-1",
                "task_id": "task-1",
            },
        ),
    ]
    assert work_item_event is calls[-1][1]

    calls.clear()
    episode_payload = {"new_status": "active"}
    episode_event = store._make_episode_event(
        EventKind.EPISODE_STATUS_CHANGED,
        "episode-1",
        episode_payload,
        work_item_id="work-1",
        task_id="task-1",
        provenance=Provenance.MODEL_HYPOTHESIS,
        lease_id="lease-1",
        owner="runner-1",
        fencing_token=7,
        event_id="event-stable",
    )
    assert calls == [
        ("now", None),
        (
            "event",
            {
                "event_id": "event-stable",
                "kind": EventKind.EPISODE_STATUS_CHANGED,
                "occurred_at": "2026-07-24T01:02:03+00:00",
                "source": "kernel",
                "provenance": Provenance.MODEL_HYPOTHESIS,
                "policy_version": "policy-test",
                "payload": episode_payload,
                "work_item_id": "work-1",
                "task_id": "task-1",
                "episode_id": "episode-1",
                "lease_id": "lease-1",
                "owner": "runner-1",
                "fencing_token": 7,
            },
        ),
    ]
    assert episode_event is calls[-1][1]


def test_episode_event_keeps_falsy_event_id_generation_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def uuid4() -> SimpleNamespace:
        calls.append("uuid4")
        return SimpleNamespace(hex="uuid-test")

    def now_iso() -> str:
        calls.append("now")
        return "2026-07-24T01:02:03+00:00"

    monkeypatch.setattr(store_module, "uuid4", uuid4)
    monkeypatch.setattr(store_module, "_now_iso", now_iso)

    event = _store()._make_episode_event(
        EventKind.EPISODE_CREATED,
        "episode-1",
        {},
        event_id="",
    )

    assert calls == ["uuid4", "now"]
    assert event.event_id == "episode.created.uuid-test"


def test_event_factory_facades_delegate_current_store_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    results = {name: object() for name in ("task", "work_item", "episode")}

    def recorder(name: str):
        def record(*args: Any, **kwargs: Any) -> Any:
            observed[name] = (args, kwargs)
            return results[name]

        return record

    for name in results:
        monkeypatch.setattr(
            store_module,
            f"_run_make_{name}_event",
            recorder(name),
            raising=False,
        )

    event_type = object()
    event_kind = SimpleNamespace(WORK_ITEM_STATUS_CHANGED=object())
    provenance_type = SimpleNamespace(MACHINE_OBSERVATION=object())
    monkeypatch.setattr(store_module, "EventEnvelope", event_type)
    monkeypatch.setattr(store_module, "EventKind", event_kind)
    monkeypatch.setattr(store_module, "Provenance", provenance_type)
    monkeypatch.setattr(store_module, "uuid4", lambda: SimpleNamespace(hex="uuid-test"))
    monkeypatch.setattr(store_module, "_now_iso", lambda: "2026-07-24T01:02:03+00:00")
    store = _store()
    payload: dict[str, Any] = {}

    assert store._make_task_event("task.kind", "task-1", payload) is results["task"]
    assert (
        store._work_item_status_event(
            "work-1", WorkItemStatus.READY, "task-1", "time-explicit"
        )
        is results["work_item"]
    )
    assert (
        store._make_episode_event(
            "episode.kind",
            "episode-1",
            payload,
            event_id="event-stable",
        )
        is results["episode"]
    )

    assert observed == {
        "task": (
            ("task.kind", "task-1", payload),
            {
                "event_id": "task.kind.uuid-test",
                "occurred_at": "2026-07-24T01:02:03+00:00",
                "provenance": Provenance.MACHINE_OBSERVATION,
                "work_item_id": None,
                "policy_version": "policy-test",
                "event_type": event_type,
            },
        ),
        "work_item": (
            ("work-1", WorkItemStatus.READY, "task-1", "time-explicit"),
            {
                "event_id": "wi.status.work-1.uuid-test",
                "event_kind": event_kind.WORK_ITEM_STATUS_CHANGED,
                "provenance": provenance_type.MACHINE_OBSERVATION,
                "policy_version": "policy-test",
                "event_type": event_type,
            },
        ),
        "episode": (
            ("episode.kind", "episode-1", payload),
            {
                "event_id": "event-stable",
                "occurred_at": "2026-07-24T01:02:03+00:00",
                "work_item_id": None,
                "task_id": None,
                "provenance": Provenance.MACHINE_OBSERVATION,
                "lease_id": None,
                "owner": None,
                "fencing_token": None,
                "policy_version": "policy-test",
                "event_type": event_type,
            },
        ),
    }


def test_event_factory_returns_real_event_envelopes() -> None:
    store = _store()

    assert isinstance(
        store._make_task_event(EventKind.TASK_CREATED, "task-1", {}),
        EventEnvelope,
    )
    assert isinstance(
        store._work_item_status_event(
            "work-1", WorkItemStatus.READY, "task-1", "time-explicit"
        ),
        EventEnvelope,
    )
    assert isinstance(
        store._make_episode_event(EventKind.EPISODE_CREATED, "episode-1", {}),
        EventEnvelope,
    )
