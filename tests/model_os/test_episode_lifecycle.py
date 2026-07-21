"""State-machine gate tests for slice-087 (codex C2 + H2; pass criteria 16, 17).

The structured Episode commands are the ONLY way to move Episode state, so they
own the frozen transition graph (spec §EpisodeStatus 状态机). codex C2 caught
that ``commit_checkpoint(next_status=<anything>)`` let a lease holder jump
straight from ``reconcile_required`` to ``active`` (bypassing the human
``resolve_reconcile`` decision) or to ``closed`` (without releasing the lease).
codex H2 caught that ``suspend_episode`` silently skipped a missing or wrong-
state Task/WorkItem, so an Episode could enter ``suspended_waiting_*`` while
its Task stayed ``running`` and kept the foreground.

This file pins:
- ``commit_checkpoint`` always targets ``checkpointing`` (no arbitrary target).
- ``close_episode`` only accepts ``checkpointing`` as its source.
- ``suspend_episode`` rejects (does not silently skip) when the bound
  WorkItem/Task is missing or not in the expected state.
- A property-style legal/illegal transition check.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    EpisodeSnapshot,
    EpisodeStatus,
)

from tests.model_os._episode_helpers import (
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
    make_running_task_episode,
)


# ----------------------------------------------------------- helpers ---


def _checkpoint(
    store: ModelOsStore,
    episode_id: str,
    lease,
    *,
    key: str = "ck-1",
    snapshot: EpisodeSnapshot | None = None,
) -> None:
    store.commit_checkpoint(
        episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=snapshot or make_cooperative_snapshot(),
        checkpoint_key=key,
    )


# ----------------------------------------------- C2: commit_checkpoint target ---


def test_commit_checkpoint_always_targets_checkpointing(
    store: ModelOsStore, monkeypatch
) -> None:
    """C2: commit_checkpoint must move the Episode to ``checkpointing`` and to
    NO other state. The previous ``next_status`` parameter let a lease holder
    pick any target — e.g. leap from reconcile_required straight to active,
    bypassing the human reconcile decision."""

    from tests.model_os._episode_helpers import FakeClock

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="winding down",
    )
    _checkpoint(store, episode.episode_id, lease)
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.CHECKPOINTING
    )


def test_commit_checkpoint_signature_has_no_arbitrary_next_status() -> None:
    """C2 structural guard: the public commit_checkpoint MUST NOT expose a
    ``next_status`` parameter. The target is fixed by the command, not chosen
    by the caller."""

    import inspect

    sig = inspect.signature(ModelOsStore.commit_checkpoint)
    assert "next_status" not in sig.parameters, (
        "commit_checkpoint must not accept next_status; the target state is "
        "fixed by the command (checkpointing), not caller-chosen"
    )


def test_commit_checkpoint_refuses_from_reconcile_required(
    store: ModelOsStore, monkeypatch
) -> None:
    """C2 core bypass: an Episode in ``reconcile_required`` must NOT be movable
    by ``commit_checkpoint`` at all. ``reconcile_required`` is a blocked state;
    its only exit is ``resolve_reconcile`` (a human decision). Previously a
    lease holder could pass ``next_status=ACTIVE`` here and silently resume."""

    from tests.model_os._episode_helpers import FakeClock
    from trowel_py.model_os.types import (
        PendingDescriptor,
        ReconcileReason,
        WaitingSubtype,
    )

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=PendingDescriptor(
            kind=WaitingSubtype.INPUT,
            native_generation="g1",
            correlation_id="c1",
            cause="need input",
            posed_at="2026-07-21T00:00:05Z",
        ),
    )
    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )

    with pytest.raises(EpisodeCommandError):
        _checkpoint(store, episode.episode_id, lease, key="ck-reconcile")
    # state untouched
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )


# ------------------------------------------------- C2: close_episode source ---


def test_close_episode_only_from_checkpointing(store: ModelOsStore, monkeypatch) -> None:
    """C2: close_episode accepts ONLY ``checkpointing`` as its source. Closing
    straight from active / suspended / reconcile_required must be rejected —
    those need their own exit path (yield+checkpoint, resolve, reconcile)."""

    from tests.model_os._episode_helpers import FakeClock

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)

    # close from ACTIVE is illegal
    with pytest.raises(EpisodeCommandError):
        store.close_episode(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
        )

    # legal path: yield → checkpoint → close
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    _checkpoint(store, episode.episode_id, lease)
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


def test_close_episode_releases_ownership_lease(
    store: ModelOsStore, monkeypatch
) -> None:
    """C2 follow-on: closing must release the ownership lease in the same
    transaction, so a closed Episode has no orphan lease. (The previous
    arbitrary-next_status=CLOSED path skipped this release.)"""

    from tests.model_os._episode_helpers import FakeClock

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
    _checkpoint(store, episode.episode_id, lease)
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is None or row["released_at"] is not None, (
        "closed Episode must not keep an active ownership lease"
    )


# ------------------------------------------------- H2: suspend rejects bad state ---


def test_start_episode_rejects_unknown_work_item(store: ModelOsStore) -> None:
    """H2: start_episode must verify the WorkItem exists. A dangling
    work_item_id would otherwise let suspend's lookup silently miss and the
    Episode enter suspended with no WorkItem state change."""

    with pytest.raises(EpisodeCommandError):
        store.start_episode(
            work_item_id="does-not-exist",
            owner="runner-A",
            ttl_seconds=300,
            idempotency_key="ep-dangling",
        )


def test_suspend_rejects_when_task_not_running(
    store: ModelOsStore, monkeypatch
) -> None:
    """H2: a Task-bound Episode can only suspend when its Task is RUNNING. If
    the Task is (for example) READY or already waiting, suspend must REJECT —
    not silently skip the Task half and leave a half-suspended Episode."""

    from tests.model_os._episode_helpers import FakeClock
    from trowel_py.model_os.types import PendingDescriptor, WaitingSubtype

    clock = FakeClock()
    clock.install(monkeypatch)
    # Task driven to READY (warm) but foreground NOT claimed → Task is READY
    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="tk-1"
    )
    store.promote_to_warm(task.task_id)
    work_item_id = task.primary_work_item_id
    episode, lease = store.start_episode(
        work_item_id=work_item_id,
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="ep-1",
        task_id=task.task_id,
    )
    activate_episode(store, episode.episode_id, lease)
    # Task is READY (not RUNNING) at this point
    with pytest.raises(EpisodeCommandError):
        store.suspend_episode(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            pending=PendingDescriptor(
                kind=WaitingSubtype.INPUT,
                native_generation="g1",
                correlation_id="c1",
                cause="need input",
                posed_at="2026-07-21T00:00:05Z",
            ),
        )
    # Episode must NOT have moved to suspended
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.ACTIVE
    )


# ------------------------------------ property-style legal/illegal matrix ---


LEGAL_TRANSITIONS: dict[EpisodeStatus, frozenset[EpisodeStatus]] = {
    EpisodeStatus.STARTING: frozenset({EpisodeStatus.ACTIVE, EpisodeStatus.FAILED}),
    EpisodeStatus.ACTIVE: frozenset(
        {
            EpisodeStatus.YIELD_REQUESTED,
            EpisodeStatus.SUSPENDED_WAITING_INPUT,
            EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
            # closed is reached via checkpointing, NOT directly from active;
            # a cooperative checkpoint (active→checkpointing) is allowed.
            EpisodeStatus.CHECKPOINTING,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.YIELD_REQUESTED: frozenset(
        {EpisodeStatus.CHECKPOINTING, EpisodeStatus.FAILED, EpisodeStatus.RECOVERING}
    ),
    EpisodeStatus.CHECKPOINTING: frozenset(
        {EpisodeStatus.CLOSED, EpisodeStatus.FAILED, EpisodeStatus.RECOVERING}
    ),
    EpisodeStatus.SUSPENDED_WAITING_INPUT: frozenset(
        {
            EpisodeStatus.SUSPENDED_READY,
            EpisodeStatus.RECONCILE_REQUIRED,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.SUSPENDED_WAITING_APPROVAL: frozenset(
        {
            EpisodeStatus.SUSPENDED_READY,
            EpisodeStatus.RECONCILE_REQUIRED,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.SUSPENDED_READY: frozenset(
        {
            EpisodeStatus.ACTIVE,
            EpisodeStatus.RECONCILE_REQUIRED,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.RECONCILE_REQUIRED: frozenset(
        {
            # resolve_reconcile(close) builds a recovery snapshot + lands here
            EpisodeStatus.CLOSED,
            # recommended: recovery_partial then closed
            EpisodeStatus.CHECKPOINTING,
            # resume_safe lands here first (decision 4A), then activate→active
            EpisodeStatus.SUSPENDED_READY,
            # spec line 49 also draws the direct edge; implementations may take
            # the two-step path above instead
            EpisodeStatus.ACTIVE,
            EpisodeStatus.FAILED,
        }
    ),
    EpisodeStatus.RECOVERING: frozenset(
        {EpisodeStatus.ACTIVE, EpisodeStatus.CLOSED, EpisodeStatus.FAILED}
    ),
    EpisodeStatus.CLOSED: frozenset(),
    EpisodeStatus.FAILED: frozenset(),
}


@pytest.mark.parametrize(
    "src,target,legal",
    [
        (EpisodeStatus.ACTIVE, EpisodeStatus.YIELD_REQUESTED, True),
        (EpisodeStatus.ACTIVE, EpisodeStatus.SUSPENDED_WAITING_INPUT, True),
        (EpisodeStatus.YIELD_REQUESTED, EpisodeStatus.CHECKPOINTING, True),
        (EpisodeStatus.CHECKPOINTING, EpisodeStatus.CLOSED, True),
        (EpisodeStatus.SUSPENDED_WAITING_INPUT, EpisodeStatus.SUSPENDED_READY, True),
        (EpisodeStatus.SUSPENDED_READY, EpisodeStatus.ACTIVE, True),
        # illegal jumps the gates must refuse
        (EpisodeStatus.ACTIVE, EpisodeStatus.CLOSED, False),
        (EpisodeStatus.ACTIVE, EpisodeStatus.SUSPENDED_READY, False),
        (EpisodeStatus.RECONCILE_REQUIRED, EpisodeStatus.CLOSED, True),
        (EpisodeStatus.SUSPENDED_WAITING_INPUT, EpisodeStatus.ACTIVE, False),
    ],
)
def test_state_machine_transition_legality(src, target, legal) -> None:
    """Property-style check: the frozen transition graph decides whether a
    src→target edge is legal. The command layer must refuse illegal edges
    (this test asserts the graph itself is what the spec froze; the per-command
    enforcement is the tests above)."""

    edge_legal = target in LEGAL_TRANSITIONS.get(src, frozenset())
    assert edge_legal is legal, (
        f"expected src={src.value} → target={target.value} legal={legal}, "
        f"but the frozen graph says legal={edge_legal}"
    )


def test_terminal_states_have_no_outgoing_edge() -> None:
    """pass: closed / failed are terminal — the graph gives them no outgoing
    edge, so no command may resurrect them."""

    assert LEGAL_TRANSITIONS[EpisodeStatus.CLOSED] == frozenset()
    assert LEGAL_TRANSITIONS[EpisodeStatus.FAILED] == frozenset()


# ------------------------------------------------- H4: fail restores Task ---


def test_fail_episode_returns_task_to_ready_and_releases_foreground(
    store: ModelOsStore, monkeypatch
) -> None:
    """H4: an Episode-level failure is NOT a Task-level error. The Task must
    return to READY (spec line 50: 任一步 → failed — Task 回 ready，不进 error),
    the primary WorkItem to READY, and the foreground must be released — so
    the work can be retried later. The previous code only flipped the Episode
    to FAILED and left the Task RUNNING with the foreground held."""

    from tests.model_os._episode_helpers import (
        FakeClock,
        activate_episode,
    )
    from trowel_py.model_os.types import TaskStatus, WorkItemStatus

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    assert store.read_foreground_task_id() == task.task_id

    store.fail_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="tool broke",
    )

    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.FAILED
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.READY, (
        "Episode failure must return the Task to READY (retryable), not error"
    )
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.READY
    assert store.read_foreground_task_id() is None, (
        "Episode failure must release the foreground so another Episode can run"
    )
    # ownership lease released (no orphan lease on a failed Episode)
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is None or row["released_at"] is not None


def test_fail_episode_system_work_item_restores_running(
    store: ModelOsStore, monkeypatch
) -> None:
    """H4 system branch: a system WorkItem Episode failing must drop the
    WorkItem back to READY (no Task/foreground to touch)."""

    from tests.model_os._episode_helpers import (
        FakeClock,
        activate_episode,
        make_running_system_episode,
    )
    from trowel_py.model_os.types import WorkItemStatus

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, work_item_id = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)

    store.fail_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="broke",
    )
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.FAILED
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.READY

