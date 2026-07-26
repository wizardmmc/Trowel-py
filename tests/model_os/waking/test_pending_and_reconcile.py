from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_pending,
    make_running_task_episode,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    EpisodeRuntimeBinding,
    EpisodeStatus,
    ReconcileReason,
    TaskStatus,
)
from trowel_py.model_os.waking import (
    WakeConditionKind,
    WakeDisposition,
    WakeObservation,
)
from trowel_py.model_os.waking.reconcile import StartupReconciler


def _suspended(store: ModelOsStore, monkeypatch):
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.bind_episode_runtime(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        agent_session_id="agent-1",
        runtime="claude_code",
        native_session_id="native-1",
        runtime_generation="generation-1",
        runtime_pid=321,
        runtime_pgid=321,
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
    return episode, task


def _input(generation: str, observation_id: str = "answer-1") -> WakeObservation:
    return WakeObservation(
        observation_id=observation_id,
        kind=WakeConditionKind.USER_INPUT,
        target_ref="corr-1",
        observed_at="2026-07-21T00:00:10Z",
        source="user",
        details={"runtime_generation": generation},
    )


def test_live_generation_input_readies_same_suspended_episode(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, task = _suspended(store, monkeypatch)

    events = store.consume_wake(_input("generation-1"))

    assert events[0].disposition is WakeDisposition.SUSPENDED_READY
    snapshot = store.read_snapshot()
    current = snapshot.episode_by_id(episode.episode_id)
    assert current is not None
    assert current.status is EpisodeStatus.SUSPENDED_READY
    assert current.native_session_id == "native-1"
    task_state = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    assert task_state.status is TaskStatus.READY
    assert snapshot.foreground_task_id is None


def test_wrong_generation_closes_old_request_without_readying_task(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, task = _suspended(store, monkeypatch)

    events = store.consume_wake(_input("generation-2"))

    assert events[0].disposition is WakeDisposition.UNKNOWN_REQUIRES_USER_RESTART
    snapshot = store.read_snapshot()
    current = snapshot.episode_by_id(episode.episode_id)
    assert current is not None
    assert current.status is EpisodeStatus.RECONCILE_REQUIRED
    assert current.reconcile_reason is ReconcileReason.REQUIRES_USER_RESTART
    task_state = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    assert task_state.status is TaskStatus.WAITING_USER


@dataclass
class FakeRuntimeReconciler:
    outcomes: list[EpisodeRuntimeBinding]

    def reconcile(self, binding: EpisodeRuntimeBinding) -> str:
        self.outcomes.append(binding)
        return "killed_private_process_group"


def test_startup_reconciles_runtime_before_invalidating_pending(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, _ = _suspended(store, monkeypatch)
    runtime = FakeRuntimeReconciler([])
    reconciler = StartupReconciler(store, runtime_reconciler=runtime)

    results = reconciler.run()

    assert results == ((episode.episode_id, "killed_private_process_group"),)
    assert runtime.outcomes[0].runtime_pid == 321
    current = store.read_snapshot().episode_by_id(episode.episode_id)
    assert current is not None
    assert current.status is EpisodeStatus.RECONCILE_REQUIRED
    assert current.reconcile_reason is ReconcileReason.REQUIRES_USER_RESTART


def test_startup_reconcile_unknown_cleanup_does_not_guess_success(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, _ = _suspended(store, monkeypatch)

    class UnknownRuntime:
        def reconcile(self, _binding: EpisodeRuntimeBinding) -> str:
            return "unknown_requires_reconcile"

    results = StartupReconciler(store, runtime_reconciler=UnknownRuntime()).run()

    assert results == ((episode.episode_id, "unknown_requires_reconcile"),)
    current = store.read_snapshot().episode_by_id(episode.episode_id)
    assert current is not None
    assert current.status is EpisodeStatus.RECONCILE_REQUIRED
    assert current.reconcile_reason is ReconcileReason.REQUIRES_USER_RESTART


def test_startup_reconcile_survives_store_reopen(
    db_path: Path, monkeypatch
) -> None:
    bootstrap = ModelOsStore(db_path)
    bootstrap.open()
    episode, _ = _suspended(bootstrap, monkeypatch)
    bootstrap.close()
    reopened = ModelOsStore(db_path)
    reopened.open()
    runtime = FakeRuntimeReconciler([])
    try:
        results = StartupReconciler(
            reopened, runtime_reconciler=runtime
        ).run()
        assert results == ((episode.episode_id, "killed_private_process_group"),)
        current = reopened.read_snapshot().episode_by_id(episode.episode_id)
        assert current is not None
        assert current.status is EpisodeStatus.RECONCILE_REQUIRED
    finally:
        reopened.close()
