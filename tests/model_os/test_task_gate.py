"""Command gate, completion authority and payload hygiene (slice-086).

Covers spec pass criteria 5, 10, 12 + original_goal immutability:
- the structured entry point writes USER_DECISION at the boundary; there is
  no provenance parameter to forge (grill decision 5) (5)
- a USER_REQUEST task's done requires USER_DECISION; MODEL_HYPOTHESIS self-
  report is refused (10)
- task.created payload carries the goal, not conversation transcript (12)
- original_goal is immutable; append_constraint only appends
- TASK_CREATION_DENIED is audit-only (reducer no-op)
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import ModelOsStore, TaskCommandError
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    Provenance,
    TaskOrigin,
)


# ----------------------------------------------- structured gate (5,10) ---


def test_entry_point_records_user_decision(store: ModelOsStore) -> None:
    """``create_task_from_user_request`` is the trusted boundary: it writes
    USER_DECISION + USER_REQUEST. There is no ``provenance=`` parameter a
    caller could set to MODEL_HYPOTHESIS (grill decision 5)."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="d"
    )
    events = store.list_events()
    created = next(e for _, e in events if e.kind == EventKind.TASK_CREATED)
    assert created.provenance == Provenance.USER_DECISION

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.origin == TaskOrigin.USER_REQUEST


def test_user_task_done_rejects_model_self_report(store: ModelOsStore) -> None:
    """A USER_REQUEST task's completion requires USER_DECISION; a model
    self-report (MODEL_HYPOTHESIS) cannot close it (pass criterion 10)."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="d"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    with pytest.raises(TaskCommandError):
        store.complete_task(
            task.task_id,
            confirmed_by="model",
            confirmation_provenance=Provenance.MODEL_HYPOTHESIS,
        )
    # Task stays running, foreground still held (the command refused)
    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    from trowel_py.model_os.types import TaskStatus

    assert t.status == TaskStatus.RUNNING
    assert snap.foreground_task_id == task.task_id


def test_creation_denied_event_is_audit_only(store: ModelOsStore) -> None:
    """TASK_CREATION_DENIED is audit-only (reducer no-op): it records a
    refused MODEL_HYPOTHESIS creation attempt but derives no task state. v0
    has no model-creation entry point yet (slice-097 wires the candidate
    adoption channel); this pins the reducer semantics."""

    store.append_event(
        EventEnvelope(
            event_id="deny-1",
            kind=EventKind.TASK_CREATION_DENIED,
            occurred_at="2026-07-21T00:00:00Z",
            source="kernel",
            provenance=Provenance.MODEL_HYPOTHESIS,
            policy_version="v0",
            payload={"reason": "model cannot create task directly"},
        )
    )
    snap = store.read_snapshot()
    assert snap.tasks == ()
    assert "task.creation_denied" not in snap.unrecognized_event_kinds


# ----------------------------------------------- payload hygiene (12) ---


def test_created_payload_carries_no_transcript(store: ModelOsStore) -> None:
    """task.created stores the goal + structural fields only; conversation
    transcript never lands in the event payload (pass criterion 12)."""

    store.create_task_from_user_request(
        original_goal="完成 slice-086",
        idempotency_key="k",
        authorization_scope="d",
    )
    events = store.list_events()
    created = next(e for _, e in events if e.kind == EventKind.TASK_CREATED)
    assert created.payload["original_goal"] == "完成 slice-086"
    for forbidden in ("messages", "transcript", "conversation", "prompt", "history"):
        assert forbidden not in created.payload


# ------------------------------------- original_goal immutability ---


def test_original_goal_immutable_and_constraints_appended(
    store: ModelOsStore,
) -> None:
    """original_goal is frozen at creation; append_constraint only appends,
    never overwrites (so the system can always explain where the Task came
    from)."""

    task = store.create_task_from_user_request(
        original_goal="原始目标", idempotency_key="k", authorization_scope="d"
    )
    store.append_constraint(task.task_id, "约束1")
    store.append_constraint(task.task_id, "约束2")

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.original_goal == "原始目标"
    assert t.appended_constraints == ("约束1", "约束2")


def test_error_task_rejects_further_commands(store: ModelOsStore) -> None:
    """Terminal Task (error) rejects lifecycle commands; no auto-recovery
    (Temporal workflow-failure semantics)."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="d"
    )
    store.record_task_error(task.task_id, reason="dependency overturned")
    with pytest.raises(TaskCommandError):
        store.promote_to_warm(task.task_id)


def test_authorization_change_records_user_decision(store: ModelOsStore) -> None:
    """change_authorization is a USER_DECISION event with a ``confirmed_by``
    audit field; authority comes from the command boundary, not from
    provenance forgery."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="default"
    )
    store.change_authorization(
        task.task_id, authorization_scope="elevated", confirmed_by="user"
    )
    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.authorization_scope == "elevated"
    events = store.list_events()
    auth_ev = next(
        e for _, e in events if e.kind == EventKind.TASK_AUTHORIZATION_CHANGED
    )
    assert auth_ev.provenance == Provenance.USER_DECISION
    assert auth_ev.payload["confirmed_by"] == "user"


def test_authorization_change_on_terminal_rejected(store: ModelOsStore) -> None:
    """A cancelled/done/errored Task refuses authorization changes (a terminal
    Task must not be silently re-authorised)."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="default"
    )
    store.cancel_task(task.task_id, reason="done with this")
    with pytest.raises(TaskCommandError):
        store.change_authorization(
            task.task_id,
            authorization_scope="elevated",
            confirmed_by="user",
        )


# ---------------------------------- append_event guard + state guards (HIGH 1/2) ---


def test_append_event_rejects_task_lifecycle_kinds(store: ModelOsStore) -> None:
    """Public append_event refuses Task lifecycle kinds — a caller that
    somehow obtains a Store handle cannot forge a Task / completed / claim
    event to bypass the command gates (codex review HIGH 1)."""

    forged = EventEnvelope(
        event_id="forge-1",
        kind=EventKind.TASK_COMPLETED,
        occurred_at="2026-07-21T00:00:00Z",
        source="attacker",
        provenance=Provenance.MODEL_HYPOTHESIS,
        policy_version="v0",
        payload={"confirmed_by": "x", "confirmation_provenance": "user_decision"},
        task_id="nonexistent",
    )
    with pytest.raises(TaskCommandError):
        store.append_event(forged)


def test_claim_foreground_rejects_waiting_state(store: ModelOsStore) -> None:
    """claim_foreground requires READY; a waiting_user task (still warm) cannot
    leap straight to running (codex review HIGH 2)."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="d"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.set_waiting_user(
        task.task_id, cause="等回复", correlation_id="q1"
    )  # running → waiting_user (foreground released)
    with pytest.raises(TaskCommandError):
        store.claim_foreground(task.task_id)


def test_complete_rejects_non_running_state(store: ModelOsStore) -> None:
    """complete_task requires RUNNING; a ready Task cannot be completed
    directly (codex review HIGH 2)."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="d"
    )
    store.promote_to_warm(task.task_id)  # READY, not running
    with pytest.raises(TaskCommandError):
        store.complete_task(
            task.task_id, confirmed_by="user", evidence_refs=("evidence-1",)
        )


def test_complete_rejects_empty_evidence(store: ModelOsStore) -> None:
    """complete_task requires non-empty evidence_refs (model self-report is not
    sufficient — codex review M2)."""

    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="k", authorization_scope="d"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    with pytest.raises(TaskCommandError):
        store.complete_task(
            task.task_id, confirmed_by="user", evidence_refs=()
        )
