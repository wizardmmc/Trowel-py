from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventKind
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
)


def test_record_side_effect_deduplicates_action_and_key(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)

    def record() -> None:
        store.record_side_effect(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            action_ref="action/send-email",
            idempotency_key="se-key-1",
            outcome="done",
            evidence_ref="evidence/sent.txt",
        )

    record()
    record()

    recorded = [
        event
        for _, event in store.list_events()
        if event.kind == EventKind.EPISODE_SIDE_EFFECT_RECORDED
        and event.episode_id == episode.episode_id
    ]
    assert len(recorded) == 1
