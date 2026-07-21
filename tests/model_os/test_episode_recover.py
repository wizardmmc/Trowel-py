"""recover_episode atomicity tests (slice-087 pass 1, 2; codex H5).

``recover_episode`` is the crash-recovery entry point: a new runner takes over
an Episode whose ownership lease expired. codex H5 caught that the previous
implementation split the takeover and the RECOVERING transition across TWO
transactions — a crash between them left an orphan lease (on a missing or
terminal Episode, or with the RECOVERING event never written).

The fix puts the whole operation in ONE IMMEDIATE transaction: verify the
Episode exists and is non-terminal → take over the expired lease (mark old
released + INSERT new + bump the fence counter) → write the fenced RECOVERING
event with the fresh token. Any failure rolls the lease back too.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import (
    EpisodeCommandError,
    LeaseConflict,
    ModelOsStore,
)
from trowel_py.model_os.types import EpisodeStatus

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
)


def _episode_with_expired_lease(store, monkeypatch, *, ttl=60):
    """Start a system Episode, let its lease expire. Return (episode, old_lease)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=ttl)
    activate_episode(store, episode.episode_id, lease)
    clock.advance(ttl + 1)  # past TTL
    return episode, lease


# ----------------------------------------------- rejection cases (H5) ---


def test_recover_rejects_unknown_episode(store: ModelOsStore) -> None:
    """H5: recovering a non-existent Episode must refuse AND leave no orphan
    lease. The previous two-tx code committed the lease first, so a bad
    episode_id produced an orphan lease row pointing at nothing."""

    with pytest.raises(EpisodeCommandError):
        store.recover_episode(
            "does-not-exist",
            new_owner="runner-B",
            ttl_seconds=300,
            idempotency_key="recover-1",
            reason="restart",
        )
    row = store._conn.execute(
        "SELECT COUNT(*) AS n FROM leases WHERE resource_id='does-not-exist'"
    ).fetchone()
    assert int(row["n"]) == 0, "no orphan lease for an unknown episode"


def test_recover_rejects_terminal_episode(
    store: ModelOsStore, monkeypatch
) -> None:
    """H5: a closed/failed Episode must not be recovered. Recovering a terminal
    Episode would resurrect it."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    # close it via the legal path
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    from tests.model_os._episode_helpers import make_cooperative_snapshot

    store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-close-1",
    )
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.CLOSED
    )

    with pytest.raises(EpisodeCommandError):
        store.recover_episode(
            episode.episode_id,
            new_owner="runner-B",
            ttl_seconds=300,
            idempotency_key="recover-term",
            reason="restart",
        )


def test_recover_refuses_while_lease_still_live(
    store: ModelOsStore, monkeypatch
) -> None:
    """H5: a live (unexpired) lease cannot be taken over via recover_episode —
    that is what ``LeaseConflict`` is for. Recover is only for EXPIRED leases."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=300)
    activate_episode(store, episode.episode_id, lease)
    # do NOT advance the clock — lease still live
    with pytest.raises(LeaseConflict):
        store.recover_episode(
            episode.episode_id,
            new_owner="runner-B",
            ttl_seconds=300,
            idempotency_key="recover-live",
            reason="restart",
        )


# ----------------------------------------------- happy path (H5 atomic) ---


def test_recover_takes_over_expired_lease_atomically(
    store: ModelOsStore, monkeypatch
) -> None:
    """H5: with an expired lease, recover takes over in ONE tx — the old grant
    is marked released, a fresh lease with a HIGHER token is inserted, and the
    Episode moves to RECOVERING. The old runner's token is now stale."""

    episode, old_lease = _episode_with_expired_lease(store, monkeypatch, ttl=60)

    new_lease = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-ok",
        reason="restart",
    )
    assert new_lease.fencing_token > old_lease.fencing_token
    assert new_lease.owner == "runner-B"

    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.RECOVERING

    # old grant preserved as released history
    old_row = store._conn.execute(
        "SELECT released_at FROM leases WHERE lease_id=?", (old_lease.lease_id,)
    ).fetchone()
    assert old_row["released_at"] is not None

    # the old runner can no longer write (fencing). Check at the ownership
    # primitive directly — request_yield would refuse on status (RECOVERING)
    # before reaching the fencing check, which would not exercise the token.
    from trowel_py.model_os.store import StaleWriterRejected

    with pytest.raises(StaleWriterRejected):
        store._check_ownership_in_tx(
            episode.episode_id,
            old_lease.lease_id,
            old_lease.owner,
            old_lease.fencing_token,
        )


def test_recover_is_idempotent_on_retry(
    store: ModelOsStore, monkeypatch
) -> None:
    """A retry of recover_episode with the same idempotency_key + owner returns
    the same lease; it does not mint a third grant or a second RECOVERING."""

    episode, _ = _episode_with_expired_lease(store, monkeypatch, ttl=60)
    first = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-idem",
        reason="restart",
    )
    second = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-idem",
        reason="restart",
    )
    assert first.lease_id == second.lease_id
    assert first.fencing_token == second.fencing_token


def test_recover_then_checkpoint_recovery_partial(
    store: ModelOsStore, monkeypatch
) -> None:
    """End-to-end recovery: recover → RECOVERING, then the new owner writes a
    recovery_partial checkpoint with the fresh token. Confirms the new lease
    actually authorises fenced writes after takeover."""


    episode, _ = _episode_with_expired_lease(store, monkeypatch, ttl=60)
    new_lease = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-ck",
        reason="restart",
    )
    ref = store.checkpoint_recovery_partial(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
        reason="post-crash recovery",
        checkpoint_key="recover-ck-1",
    )
    assert ref.episode_id == episode.episode_id
    assert ref.version >= 1
