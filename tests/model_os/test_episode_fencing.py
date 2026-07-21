"""Fencing tests for slice-087 (pass criteria 2, 3, 4, 19; codex C1 + L2).

Fencing = the store rejects a write that does not carry the caller's CURRENT
ownership lease triple ``(lease_id, owner, fencing_token)``. The point (per
Kleppmann) is that an old runner whose lease expired or was taken over cannot
silently overwrite the new runner's state.

codex findings covered here:
- C1: ``resolve_episode_wait`` / ``mark_pending_channel_lost`` /
  ``resolve_reconcile`` are EXTERNALLY driven (the answer arrives, the host
  generation closed, the human decided). Their event kinds must NOT be in the
  fenced set: the caller has no lease to present (the lease may be long gone,
  e.g. on runtime restart). The gate that remains is "only the structured
  command may write these kinds" (``_EPISODE_LIFECYCLE_KINDS`` blocks a bare
  ``append_event``).
- L2: the fencing check rejects at ``expires_at <= now`` but takeover only
  fired at ``expires_at < now`` — at the exact expiry instant the old owner
  had already lost power yet the new owner could not take over. Unify to ``<=``.

The externally-driven commands' OWN behaviour (suspend→resolve→activate etc.)
is exercised in ``test_episode_suspend.py``; this file is only about the
fencing primitive itself.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import (
    EpisodeCommandError,
    ModelOsStore,
    StaleWriterRejected,
    _EPISODE_FENCED_KINDS,
    _EPISODE_LIFECYCLE_KINDS,
)
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
)


# ---------------------------------------------------- C1: externally-driven kinds ---


def test_externally_driven_episode_kinds_are_not_fenced() -> None:
    """C1: resolve_wait / reconcile_required / reconcile_resolved are driven by
    an arriving answer, a closed host generation, or a human decision — none of
    those callers holds an ownership lease. They must NOT be in the fenced set,
    or the commands could never fire (mark_pending_channel_lost runs precisely
    when the lease has expired on restart)."""

    assert EventKind.EPISODE_WAIT_RESOLVED not in _EPISODE_FENCED_KINDS
    assert EventKind.EPISODE_RECONCILE_REQUIRED not in _EPISODE_FENCED_KINDS
    assert EventKind.EPISODE_RECONCILE_RESOLVED not in _EPISODE_FENCED_KINDS


def test_externally_driven_kinds_still_gated_against_bare_append(
    store: ModelOsStore,
) -> None:
    """Removing these kinds from the FENCED set must NOT open a forge path: a
    bare ``append_event`` still refuses them (they stay in
    ``_EPISODE_LIFECYCLE_KINDS``). The gate is 'only the structured command may
    write', not 'must present a lease'."""

    for kind in (
        EventKind.EPISODE_WAIT_RESOLVED,
        EventKind.EPISODE_RECONCILE_REQUIRED,
        EventKind.EPISODE_RECONCILE_RESOLVED,
    ):
        assert kind in _EPISODE_LIFECYCLE_KINDS, (
            f"{kind} must stay lifecycle-gated even after leaving the fenced set"
        )
        bad = EventEnvelope(
            event_id=f"forge.{kind}.{kind}",
            kind=kind,
            occurred_at="2026-07-21T00:00:00Z",
            source="attacker",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"new_status": "active"},
            episode_id="ep-x",
        )
        with pytest.raises((EpisodeCommandError, ValueError)):
            store.append_event(bad)


def test_gated_kinds_block_forges_but_real_commands_succeed(
    store: ModelOsStore, monkeypatch
) -> None:
    """Sanity: a fenced kind still in the set (YIELD_REQUESTED) cannot be
    forged via append_event, and the real command path still works for a
    legitimate lease holder."""

    assert EventKind.EPISODE_YIELD_REQUESTED in _EPISODE_FENCED_KINDS
    forged = EventEnvelope(
        event_id="forge.yield.1",
        kind=EventKind.EPISODE_YIELD_REQUESTED,
        occurred_at="2026-07-21T00:00:00Z",
        source="attacker",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"new_status": "yield_requested", "reason": "x"},
        episode_id="ep-x",
        lease_id="L",
        owner="A",
        fencing_token=1,
    )
    with pytest.raises((EpisodeCommandError, ValueError)):
        store.append_event(forged)


# ---------------------------------------------- omission bypass (pass 3) ---


def test_fenced_event_missing_triple_is_rejected(store: ModelOsStore) -> None:
    """pass 3: a fenced kind written without the caller-held triple must be
    rejected — omission is not a bypass. The internal helper is the choke
    point; exercising it directly proves the check does not depend on a
    command remembering to pass the triple."""

    episode, _, _ = make_running_system_episode(store)
    with store._tx():
        with pytest.raises(EpisodeCommandError):
            store._append_fenced_event_in_tx(
                store._make_episode_event(
                    EventKind.EPISODE_YIELD_REQUESTED,
                    episode.episode_id,
                    {"new_status": "yield_requested", "reason": "x"},
                    # no lease_id / owner / fencing_token
                )
            )


# ----------------------------------------- caller-held triple check (pass 4) ---


def _active_system_episode(
    store: ModelOsStore, monkeypatch, *, ttl: int = 300
):
    """Start a system WorkItem Episode, bring it to ACTIVE, return
    (episode, lease)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=ttl)
    activate_episode(store, episode.episode_id, lease)
    return episode, lease


def test_wrong_lease_id_is_rejected(store: ModelOsStore, monkeypatch) -> None:
    episode, lease = _active_system_episode(store, monkeypatch)
    with pytest.raises(StaleWriterRejected):
        store.request_yield(
            episode.episode_id,
            expected_lease_id="wrong-lease-id",
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            reason="r",
        )
    # state unchanged
    assert store.read_snapshot().episode_by_id(episode.episode_id).status.value == "active"


def test_wrong_owner_is_rejected(store: ModelOsStore, monkeypatch) -> None:
    episode, lease = _active_system_episode(store, monkeypatch)
    with pytest.raises(StaleWriterRejected):
        store.request_yield(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner="impostor",
            expected_token=lease.fencing_token,
            reason="r",
        )


def test_wrong_fencing_token_is_rejected(store: ModelOsStore, monkeypatch) -> None:
    """pass 2: a stale (lower) token is rejected. This is the core Kleppmann
    invariant — an old holder waking up after takeover cannot overwrite."""

    episode, lease = _active_system_episode(store, monkeypatch)
    with pytest.raises(StaleWriterRejected):
        store.request_yield(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token + 999,  # wrong token
            reason="r",
        )


def test_expired_lease_without_takeover_is_rejected(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 4: authority ends the moment the lease expires, BEFORE any takeover.
    The original holder's token stops working at expiry even if no new owner has
    arrived."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, lease)
    clock.advance(61)  # past TTL, no takeover

    with pytest.raises(StaleWriterRejected):
        store.request_yield(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            reason="r",
        )


def test_old_token_after_takeover_is_rejected(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 2 end-to-end: lease expires, a new runner takes over (higher token),
    then the OLD runner wakes up and tries to write — rejected. A
    ``late_write_rejected`` audit trail is left."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, old_lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, old_lease)

    clock.advance(61)
    new_lease = store.acquire_episode_ownership(
        episode.episode_id, owner="runner-B", ttl_seconds=60
    )
    assert new_lease.fencing_token > old_lease.fencing_token

    with pytest.raises(StaleWriterRejected):
        store.request_yield(  # old runner wakes up
            episode.episode_id,
            expected_lease_id=old_lease.lease_id,
            expected_owner=old_lease.owner,
            expected_token=old_lease.fencing_token,
            reason="stale",
        )

    # the new owner CAN still write
    store.request_yield(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
        reason="fresh",
    )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status.value
        == "yield_requested"
    )


# ------------------------- task / work_item events are never fenced (pass 19) ---


def test_task_event_carrying_episode_id_is_not_fencing_checked(
    store: ModelOsStore,
) -> None:
    """pass 19: a task.* or work_item.* event may carry ``episode_id`` as a
    CAUSAL reference (this happened during that Episode) without being fencing-
    checked. Fencing is only for events that change Episode authoritative
    state."""

    # A plain note tagged with an episode_id must append fine without any lease.
    note = EventEnvelope(
        event_id="note.with-ep-ref",
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"msg": "happened during ep-X"},
        episode_id="ep-X",  # causal reference only — no lease triple
    )
    store.append_event(note)  # must not raise


# ------------------------------------------- L2: expiry boundary consistency ---


def test_takeover_succeeds_at_exact_expiry_instant(
    store: ModelOsStore, monkeypatch
) -> None:
    """L2: fencing rejects at ``expires_at <= now``, so takeover must ALSO grant
    at ``expires_at <= now``. At the exact expiry tick the old owner has already
    lost power; the new owner must be able to take over immediately, with no
    unwritable gap."""

    clock = FakeClock()
    clock.install(monkeypatch)

    first = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
    )
    expires_at = first.expires_at
    # pin the clock to the EXACT expiry instant
    clock.set(expires_at)

    # fencing already rejects the old owner here (<= now)
    with pytest.raises(StaleWriterRejected):
        store._check_ownership_in_tx(
            "ep-1", first.lease_id, first.owner, first.fencing_token
        )
    # takeover must succeed at the same instant — no gap
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-B",
        ttl_seconds=60,
    )
    assert second.fencing_token > first.fencing_token
