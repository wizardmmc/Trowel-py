from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    Provenance,
    WorkItemKind,
)
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
)


def test_n7_read_rejects_forged_committed_event_id(
    store: ModelOsStore, monkeypatch
) -> None:
    from trowel_py.model_os.types import SnapshotRef

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    ref = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-n7",
    )
    # 使用真实存在但无关的 event，区分“记录存在”与“引用绑定一致”。
    store.append_event(
        EventEnvelope(
            event_id="note.unrelated",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"msg": "unrelated"},
        )
    )
    forged = SnapshotRef(
        episode_id=ref.episode_id,
        version=ref.version,
        committed_event_id="note.unrelated",
        payload_hash=ref.payload_hash,
    )
    with pytest.raises(EpisodeCommandError):
        store.read_episode_snapshot(forged)


def test_n8_start_episode_refuses_incubation_work_item_without_task_id(
    store: ModelOsStore,
) -> None:
    work_item = store.create_work_item(
        kind=WorkItemKind.INCUBATION,
        owner_ref="system",
        task_id="task-incubate-1",
        session_purpose=__import__(
            "trowel_py.model_os.types", fromlist=["SessionPurpose"]
        ).SessionPurpose.INCUBATION,
        memory_eligibility=__import__(
            "trowel_py.model_os.types", fromlist=["MemoryEligibility"]
        ).MemoryEligibility.INELIGIBLE,
    )
    with pytest.raises(EpisodeCommandError):
        store.start_episode(
            work_item_id=work_item.work_item_id,
            owner="runner-A",
            ttl_seconds=300,
            idempotency_key="ep-incubate",
            task_id=None,
        )


def test_non_terminal_episode_query_filters_by_work_item(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    first, _, work_item_id = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-A"
    )
    other, _, _ = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-B"
    )
    second, _ = store.start_episode(
        work_item_id=work_item_id,
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="ep-same-wi",
        task_id=None,
    )

    non_terminal = store.read_snapshot().non_terminal_episodes_for_work_item(
        work_item_id
    )
    episode_ids = {episode.episode_id for episode in non_terminal}
    assert episode_ids == {first.episode_id, second.episode_id}
    assert other.episode_id not in episode_ids
