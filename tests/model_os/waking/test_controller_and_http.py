from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_pending,
    make_running_task_episode,
)
from tests.model_os.waking.support import running_task
from trowel_py.model_os.routes import router
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeStatus, TaskStatus
from trowel_py.model_os.waking.controller import WakeController


def _suspended(store: ModelOsStore, monkeypatch):
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.bind_episode_runtime(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        agent_session_id="agent-1",
        runtime="codex",
        native_session_id="thread-1",
        runtime_generation="generation-1",
        runtime_pid=None,
        runtime_pgid=None,
        correlation_id="start-1",
        activate=True,
    )
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=make_pending(cause="need input", native_generation="generation-1"),
    )
    return episode


def test_pending_input_is_queued_in_memory_without_journaling_body(
    store: ModelOsStore, monkeypatch
) -> None:
    episode = _suspended(store, monkeypatch)
    controller = WakeController(store)

    queued = controller.queue_for_session(
        "agent-1",
        correlation_id="corr-1",
        runtime_generation="generation-1",
        payload={"decision": "accept-sensitive-value"},
    )

    assert queued.episode_id == episode.episode_id
    assert "accept-sensitive-value" not in str(store.list_events())
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        is EpisodeStatus.SUSPENDED_READY
    )
    assert controller.take_pending_input(
        episode.episode_id, runtime_generation="generation-1"
    ) == {"decision": "accept-sensitive-value"}
    assert (
        controller.take_pending_input(
            episode.episode_id, runtime_generation="generation-1"
        )
        is None
    )


def test_pending_input_rejects_wrong_generation_when_taken(
    store: ModelOsStore, monkeypatch
) -> None:
    episode = _suspended(store, monkeypatch)
    controller = WakeController(store)
    controller.queue_for_session(
        "agent-1",
        correlation_id="corr-1",
        runtime_generation="generation-1",
        payload={"decision": "accept"},
    )

    assert (
        controller.take_pending_input(
            episode.episode_id, runtime_generation="generation-2"
        )
        is None
    )
    current = store.read_snapshot().episode_by_id(episode.episode_id)
    assert current is not None
    assert current.status is EpisodeStatus.RECONCILE_REQUIRED


def test_pending_input_rejects_wrong_correlation_without_changing_state(
    store: ModelOsStore, monkeypatch
) -> None:
    episode = _suspended(store, monkeypatch)
    controller = WakeController(store)

    with pytest.raises(RuntimeError, match="correlation"):
        controller.queue_for_session(
            "agent-1",
            correlation_id="old-request",
            runtime_generation="generation-1",
            payload={"decision": "accept"},
        )

    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        is EpisodeStatus.SUSPENDED_WAITING_INPUT
    )


def test_manual_wake_http_readies_task(store: ModelOsStore) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="等待人工确认",
        condition_kind="manual",
        target_ref=f"task:{task.task_id}",
    )
    app = FastAPI()
    app.include_router(router)
    app.state.model_os_wake_controller = WakeController(store)

    response = TestClient(app).post(
        "/wake",
        json={
            "observation_id": "manual-1",
            "kind": "manual",
            "target_ref": f"task:{task.task_id}",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["wakes"][0]["disposition"] == "ready"
    assert store.read_snapshot().tasks[0].status is TaskStatus.READY


def test_wake_http_rejects_machine_observation_kinds(store: ModelOsStore) -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.model_os_wake_controller = WakeController(store)

    response = TestClient(app).post(
        "/wake",
        json={
            "observation_id": "forged-file-1",
            "kind": "observed_state",
            "target_ref": "file:/tmp/private",
        },
    )

    assert response.status_code == 422
