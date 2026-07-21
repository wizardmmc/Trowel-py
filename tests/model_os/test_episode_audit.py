"""Audit + validation tests for slice-087 (codex M1, M2, M3).

- M1: a rejected stale write must leave a DURABLE ``late_write_rejected`` audit
  event. The previous code wrote the audit in the SAME transaction that then
  rolled back, so the audit vanished — there was no proof a late write happened.
- M2: snapshot payloads are size-capped and slot-validated at the write
  boundary, and reads fail-closed when a snapshot row's committed_event is
  missing from the journal.
- M3: ``checkpoint_key`` is globally unique; a hit on a DIFFERENT episode is a
  key-scope conflict, not an idempotent retry (the previous code returned a
  ref assembled from the wrong episode_id).
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    EventKind,
    SideEffectRecord,
)

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
)


# ----------------------------------------------------- M1: late_write audit ---


def test_stale_write_leaves_durable_audit(
    store: ModelOsStore, monkeypatch
) -> None:
    """M1 / pass 2: when a stale owner is rejected, a ``late_write_rejected``
    audit event must SURVIVE in the journal — proving the late write happened.
    The previous code wrote it in the same tx that rolled back, so it vanished."""

    from trowel_py.model_os.store import StaleWriterRejected

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, old_lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, old_lease)
    clock.advance(61)
    store.acquire_episode_ownership(  # takeover by a new owner
        episode.episode_id, owner="runner-B", ttl_seconds=60
    )
    with pytest.raises(StaleWriterRejected):
        store.request_yield(  # old owner wakes up
            episode.episode_id,
            expected_lease_id=old_lease.lease_id,
            expected_owner=old_lease.owner,
            expected_token=old_lease.fencing_token,
            reason="stale",
        )

    audits = [
        ev for _, ev in store.list_events()
        if ev.kind == EventKind.LATE_WRITE_REJECTED
        and ev.episode_id == episode.episode_id
    ]
    assert len(audits) == 1, "late_write_rejected audit must survive the rollback"
    audit = audits[0]
    assert audit.payload["attempted_token"] == old_lease.fencing_token
    assert audit.payload["reason"]


# ----------------------------------------------------- M2: size cap + slots ---


def _active_system_episode(store, monkeypatch, *, ttl=300):
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=ttl)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    return episode, lease


def test_oversized_snapshot_payload_is_rejected(
    store: ModelOsStore, monkeypatch
) -> None:
    """M2 / spec line 224: a snapshot payload over the byte cap must be refused
    at the write boundary, not silently stored."""

    episode, lease = _active_system_episode(store, monkeypatch)
    big = make_cooperative_snapshot(current_judgment="x" * (300 * 1024))
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=big,
            checkpoint_key="ck-big",
        )


def test_too_many_next_steps_is_rejected(store: ModelOsStore, monkeypatch) -> None:
    """M2 / spec line 107: next_steps has at most 3 items."""

    episode, lease = _active_system_episode(store, monkeypatch)
    bad = make_cooperative_snapshot(
        next_steps=("a", "b", "c", "d")  # 4 > 3
    )
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=bad,
            checkpoint_key="ck-steps",
        )


def test_done_side_effect_without_evidence_is_rejected(
    store: ModelOsStore, monkeypatch
) -> None:
    """M2 / pass 7: a side effect marked done must carry an evidence_ref."""

    episode, lease = _active_system_episode(store, monkeypatch)
    bad = make_cooperative_snapshot(
        side_effects=(
            SideEffectRecord(
                action_ref="action/x",
                idempotency_key="k",
                outcome="done",
                evidence_ref=None,  # missing
            ),
        )
    )
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=bad,
            checkpoint_key="ck-noev",
        )


# ----------------------------------------------- M3: cross-episode key conflict ---


def test_checkpoint_key_conflict_across_episodes(
    store: ModelOsStore, monkeypatch
) -> None:
    """M3: ``checkpoint_key`` is globally unique. A second Episode trying to
    reuse another Episode's key must get a conflict, NOT a ref fabricated from
    the wrong episode_id."""

    clock = FakeClock()
    clock.install(monkeypatch)
    ep1, lease1, _ = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-key-A"
    )
    activate_episode(store, ep1.episode_id, lease1)
    store.request_yield(
        ep1.episode_id,
        expected_lease_id=lease1.lease_id,
        expected_owner=lease1.owner,
        expected_token=lease1.fencing_token,
        reason="done",
    )
    ref1 = store.commit_checkpoint(
        ep1.episode_id,
        expected_lease_id=lease1.lease_id,
        expected_owner=lease1.owner,
        expected_token=lease1.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="shared-key",
    )
    assert ref1.episode_id == ep1.episode_id

    # a SECOND episode tries the SAME key
    ep2, lease2, _ = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-key-B"
    )
    activate_episode(store, ep2.episode_id, lease2)
    store.request_yield(
        ep2.episode_id,
        expected_lease_id=lease2.lease_id,
        expected_owner=lease2.owner,
        expected_token=lease2.fencing_token,
        reason="done",
    )
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            ep2.episode_id,
            expected_lease_id=lease2.lease_id,
            expected_owner=lease2.owner,
            expected_token=lease2.fencing_token,
            snapshot=make_cooperative_snapshot(),
            checkpoint_key="shared-key",  # reuse
        )


# ----------------------------------------------- M2: read fails closed on orphan ---


def test_read_snapshot_fails_closed_when_committed_event_missing(
    store: ModelOsStore, monkeypatch
) -> None:
    """M2 / spec line 222: a snapshot row whose committed_event is not in the
    journal is corruption. Reading it must fail closed (not return an unattested
    payload)."""

    episode, lease = _active_system_episode(store, monkeypatch)
    ref = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-clean",
    )
    # tamper: delete the committed event, leaving an orphan snapshot row
    store._conn.execute(
        "DELETE FROM events WHERE event_id=?", (ref.committed_event_id,)
    )
    with pytest.raises(EpisodeCommandError):
        store.read_episode_snapshot(ref)
