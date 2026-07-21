"""Shared fixtures and builders for slice-087 Episode tests.

These helpers are deliberately bug-agnostic: they construct inputs (a running
Task+Episode, a snapshot, a fake clock) the way the SPEC says they should look,
so a test that fails is a real spec violation, not an artefact of the helper.

Design notes:
- ``FakeClock`` controls BOTH ``store._now_iso`` (used by the expiry check
  ``expires_at <= now_str``) and ``store.datetime`` (used by ``acquire_lease``
  to compute ``expires_at = datetime.now(...) + ttl``). Patching only one would
  let the other leak real wall-clock time and make TTL assertions flaky.
- The running-Task helper drives the real public command path
  (``create_task_from_user_request`` → ``promote_to_warm`` →
  ``claim_foreground``) so the Episode is suspended/resumed against a Task that
  genuinely holds foreground, exactly as 090 will.
- ``make_running_system_episode`` exercises the no-Task (system WorkItem)
  branch. WORK_ITEM_STATUS_CHANGED is not a gated kind, so a system WorkItem is
  moved to RUNNING via a plain ``append_event`` (there is no public command for
  a Task-less WorkItem lifecycle yet).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from trowel_py.model_os import store as _store_mod
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    ArtifactRef,
    Episode,
    EpisodeSnapshot,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    PendingDescriptor,
    Provenance,
    SessionPurpose,
    SideEffectRecord,
    SnapshotSource,
    Task,
    WaitingSubtype,
    WorkItemKind,
    WorkItemStatus,
)

# A fixed, memorable epoch. Tests advance from here so TTL arithmetic is
# deterministic and never depends on real wall-clock time.
_BASE_NOW = _dt.datetime(2026, 7, 21, 0, 0, 0, tzinfo=_dt.timezone.utc)


class FakeClock:
    """Controllable clock that replaces both time sources the store reads.

    ``install`` patches the store module so every ``_now_iso()`` call and every
    ``datetime.now(...)`` call inside the store returns the clock's current
    value. ``advance`` moves it forward; ``set`` jumps to an ISO literal.
    """

    def __init__(self, start: _dt.datetime = _BASE_NOW) -> None:
        self._now = start

    @classmethod
    def from_iso(cls, iso: str) -> "FakeClock":
        return cls(_dt.datetime.fromisoformat(iso))

    def advance(self, seconds: float) -> None:
        self._now = self._now + _dt.timedelta(seconds=seconds)

    def set(self, iso: str) -> None:
        self._now = _dt.datetime.fromisoformat(iso)

    def now_iso(self) -> str:
        return self._now.isoformat()

    def install(self, monkeypatch: Any) -> None:
        """Patch ``store._now_iso`` and ``store.datetime`` to this clock."""

        captured = self

        class _FakeDateTime(_dt.datetime):
            """datetime subclass whose ``now()`` returns the fake clock.

            Arithmetic (``+ timedelta``) returns a base ``datetime`` instance,
            which is fine — callers only invoke ``.isoformat()`` on the result.
            Never instantiated directly, so datetime's strict ``__new__`` never
            bites.
            """

            @classmethod
            def now(cls, tz: Any = None) -> _dt.datetime:  # type: ignore[override]
                moment = captured._now
                if tz is not None:
                    moment = moment.astimezone(tz)
                return moment

        monkeypatch.setattr(_store_mod, "_now_iso", self.now_iso)
        monkeypatch.setattr(_store_mod, "datetime", _FakeDateTime)


# --------------------------------------------------------------- builders ---


def make_pending(
    *,
    kind: WaitingSubtype = WaitingSubtype.INPUT,
    correlation_id: str = "corr-1",
    cause: str = "need user input",
    posed_at: str = "2026-07-21T00:00:05Z",
    native_generation: str | None = "gen-1",
) -> PendingDescriptor:
    """Build a PendingDescriptor with spec-valid defaults."""

    return PendingDescriptor(
        kind=kind,
        native_generation=native_generation,
        correlation_id=correlation_id,
        cause=cause,
        posed_at=posed_at,
    )


def make_cooperative_snapshot(
    *,
    work_item_goal: str = "完成 Episode 的工作",
    task_constraints_ref: str | None = None,
    current_judgment: str = "进展到一半",
    completed_with_evidence: tuple[tuple[str, str], ...] = (
        ("action/read-config", "evidence/config-dump.txt"),
    ),
    side_effects: tuple[SideEffectRecord, ...] = (
        SideEffectRecord(
            action_ref="action/write-file",
            idempotency_key="se-key-1",
            outcome="done",
            evidence_ref="evidence/written.txt",
        ),
    ),
    unknowns: tuple[str, ...] = ("不确定边界的另一侧状态",),
    next_steps: tuple[str, ...] = ("核查外部动作结果",),
    artifacts: tuple[ArtifactRef, ...] = (
        ArtifactRef(kind="commit", ref="abc123"),
    ),
    native_transcript_ref: str | None = "transcript://ep-1",
    journal_through_seq: int = 0,
) -> EpisodeSnapshot:
    """Build a COOPERATIVE snapshot with sensible, content-rich defaults.

    Defaults are deliberately non-code-jargon (生活/调研 language) per the spec
    testing method: snapshot slots must not read as if they depend on source
    identifiers. Tests that need specific slots override them.
    """

    return EpisodeSnapshot(
        work_item_goal=work_item_goal,
        task_constraints_ref=task_constraints_ref,
        current_judgment=current_judgment,
        completed_with_evidence=completed_with_evidence,
        side_effects=side_effects,
        unknowns=unknowns,
        waiting_condition=None,
        next_steps=next_steps,
        artifacts=artifacts,
        native_transcript_ref=native_transcript_ref,
        source=SnapshotSource.COOPERATIVE,
        journal_through_seq=journal_through_seq,
        base_snapshot_ref=None,
    )


# ----------------------------------------------------- running-task episode ---


def make_running_task_episode(
    store: ModelOsStore,
    *,
    owner: str = "runner-A",
    ttl_seconds: int = 300,
    goal: str = "调研一个反诈检测方案",
    idempotency_key: str = "ep-key-task-1",
    previous_snapshot_ref: Any = None,
) -> tuple[Episode, Lease, Task, str]:
    """Create a Task, drive it to RUNNING + foreground, start its Episode.

    Returns ``(episode, lease, task, work_item_id)``. The Episode is STARTING
    (090 binds native session + flips to ACTIVE); tests that need ACTIVE use
    ``activate_episode`` below.
    """

    task = store.create_task_from_user_request(
        original_goal=goal, idempotency_key=f"task-{idempotency_key}"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    episode, lease = store.start_episode(
        work_item_id=task.primary_work_item_id,  # type: ignore[arg-type]
        owner=owner,
        ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key,
        task_id=task.task_id,
        previous_snapshot_ref=previous_snapshot_ref,
    )
    return episode, lease, task, task.primary_work_item_id  # type: ignore[return-value]


def make_running_system_episode(
    store: ModelOsStore,
    *,
    owner: str = "runner-A",
    ttl_seconds: int = 300,
    kind: WorkItemKind = WorkItemKind.DEFAULT,
    idempotency_key: str = "ep-key-sys-1",
) -> tuple[Episode, Lease, str]:
    """Create a system WorkItem (no Task), move it to RUNNING, start an Episode.

    The system WorkItem has no Task, so its Episode exercises the no-Task
    branch of suspend/activate. WORK_ITEM_STATUS_CHANGED is not gated, so the
    WorkItem is moved to RUNNING via a plain status event.
    """

    work_item = store.create_work_item(
        kind=kind,
        owner_ref="system",
        task_id=None,
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )
    # Drive the system WorkItem to RUNNING. There is no public command for a
    # Task-less WorkItem lifecycle yet, so append the status event directly.
    store.append_event(
        EventEnvelope(
            event_id=f"wi.run.{work_item.work_item_id}",
            kind=EventKind.WORK_ITEM_STATUS_CHANGED,
            occurred_at=_store_mod._now_iso(),
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"new_status": WorkItemStatus.RUNNING.value},
            work_item_id=work_item.work_item_id,
        )
    )
    episode, lease = store.start_episode(
        work_item_id=work_item.work_item_id,
        owner=owner,
        ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key,
        task_id=None,
    )
    return episode, lease, work_item.work_item_id


def activate_episode(
    store: ModelOsStore, episode_id: str, lease: Lease
) -> None:
    """Move a STARTING Episode to ACTIVE via the fenced status_changed path.

    090 normally binds the native session and flips STARTING → ACTIVE. There is
    no public command for that yet (it is 090's job), so tests flip it directly
    through the fenced append path to exercise downstream commands that require
    ACTIVE. The fenced event is constructed exactly the way the real commands
    build one (caller-held lease triple).
    """

    with store._tx():
        store._append_fenced_event_in_tx(
            store._make_episode_event(
                EventKind.EPISODE_STATUS_CHANGED,
                episode_id,
                {"new_status": "active"},
                lease_id=lease.lease_id,
                owner=lease.owner,
                fencing_token=lease.fencing_token,
            )
        )
