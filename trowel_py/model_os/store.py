"""Transactional Store for the Model OS (slice-084).

Owns its own SQLite database (independent of ``trowel.db``, per the spec's
"首选独立 SQLite + WAL"). Provides the six operations the spec mandates:
transactions, CAS/lease, append event, append decision, read snapshot, and
replay by seq.

Concurrency / threading:
``check_same_thread=False`` is set from the start. FastAPI TestClient's anyio
portal runs async endpoints on a separate worker thread (see the verified
memory note on sqlite + anyio), so a connection bound to the creating thread
would explode the moment a later slice wires routes around this store. WAL +
``busy_timeout`` lets separate connections on the same file serialise writes
without immediate "database is locked" failures — the real path the
concurrency tests exercise.

Atomicity:
multi-statement appends (``append_decision_with_intent``) run inside a single
``with conn:`` block, so a crash mid-transaction rolls back every statement
(spec: "写入中断不会留下半个决定或重复 seq").

Idempotency:
``event_id`` / ``decision_id`` have UNIQUE constraints; re-appending the same
id returns the original seq instead of duplicating. ``idempotency_key`` on
leases does the same for controllable commands.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from contextlib import contextmanager
from dataclasses import replace

from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.reducer import (
    EpisodeState,
    Snapshot,
    TaskState,
    initial_snapshot,
    reduce_event,
)
from trowel_py.model_os.types import (
    ArtifactRef,
    DecisionRecord,
    Episode,
    EpisodeSnapshot,
    EpisodeStatus,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    PendingDescriptor,
    Provenance,
    ReconcileReason,
    SessionPurpose,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
    Task,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WaitingSubtype,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)
from trowel_py.model_os.context_observer import (
    ContextSample,
    context_sample_to_dict,
)

_SCHEMA_VERSION = 4  # v4 (slice-087 R3-H3): idx_leases_idem scoped to released_at IS NULL so a released lease's key does not block a new grant
_DEFAULT_POLICY_VERSION = "v0"

_LOGGER = logging.getLogger(__name__)

# slice-087 M2: upper bound on a serialized EpisodeSnapshot payload (spec
# §设计约束 line 224 mandates a size cap; the exact threshold is a spec gap,
# 256 KiB is generous for a work现场 snapshot that references — never copies —
# transcripts). Keeps a pathological payload from bloating the journal.
_MAX_SNAPSHOT_PAYLOAD_BYTES = 256 * 1024

# slice-086: Task lifecycle event kinds that must ONLY be appended by the
# structured Task commands (create_task_from_user_request / claim_foreground /
# complete_task / ...). The public ``append_event`` refuses these so a caller
# that somehow obtains a Store handle cannot bypass the command gates and forge
# a Task, resurrect a terminal Task, or fake ``running`` without a foreground
# claim. ``TASK_CREATION_DENIED`` is audit-only (reducer no-op) and stays open.
# Codex review HIGH 1.
_TASK_LIFECYCLE_KINDS = frozenset(
    {
        EventKind.TASK_CREATED,
        EventKind.TASK_STATUS_CHANGED,
        EventKind.TASK_CONSTRAINT_APPENDED,
        EventKind.TASK_WARM_CHANGED,
        EventKind.TASK_WARM_RANK_SET,
        EventKind.TASK_WAITING_SET,
        EventKind.TASK_WAITING_CLEARED,
        EventKind.TASK_AUTHORIZATION_CHANGED,
        EventKind.TASK_COMPLETED,
        EventKind.TASK_CANCELLED,
        EventKind.TASK_ERROR_RECORDED,
        EventKind.FOREGROUND_CLAIMED,
        EventKind.FOREGROUND_RELEASED,
    }
)

# slice-087: Episode lifecycle event kinds that must ONLY be appended by the
# structured Episode commands. The public ``append_event`` refuses these so a
# caller holding a Store handle cannot forge an Episode, fake a checkpoint,
# or write a stale ownership transition. ``LATE_WRITE_REJECTED`` is audit-only
# but is also gated: only the fencing path writes it (via the internal
# ``_insert_event_in_tx``), never a bare ``append_event``.
_EPISODE_LIFECYCLE_KINDS = frozenset(
    {
        EventKind.EPISODE_CREATED,
        EventKind.EPISODE_STATUS_CHANGED,
        EventKind.EPISODE_OWNERSHIP_ACQUIRED,
        EventKind.EPISODE_OWNERSHIP_RELEASED,
        EventKind.EPISODE_YIELD_REQUESTED,
        EventKind.EPISODE_CHECKPOINT_COMMITTED,
        EventKind.EPISODE_CLOSED,
        EventKind.EPISODE_FAILED,
        EventKind.EPISODE_SUSPENDED,
        EventKind.EPISODE_WAIT_RESOLVED,
        EventKind.EPISODE_ACTIVATED,
        EventKind.EPISODE_RECONCILE_REQUIRED,
        EventKind.EPISODE_RECONCILE_RESOLVED,
        EventKind.EPISODE_RECOVERING,
        EventKind.EPISODE_SIDE_EFFECT_RECORDED,
        EventKind.LATE_WRITE_REJECTED,
    }
)

# slice-087: event kinds that change Episode authoritative state while an
# ownership lease is held. These MUST carry the caller-held
# ``(lease_id, owner, fencing_token)``; the store validates them against the
# live lease before persisting. Created / ownership-acquired / ownership-
# released are excluded: they are the lease-lifecycle bookends (no lease held
# yet, or the act of acquiring/releasing itself), not fenced progress writes.
# ``LATE_WRITE_REJECTED`` is audit and never fenced. ``task.*`` / ``work_item.*``
# events that happen to carry ``episode_id`` as a causal reference are NOT in
# this set and are never fencing-checked.
#
# codex C1 (2026-07-21): EXTERNALLY-driven kinds are NOT in this set.
# ``EPISODE_WAIT_RESOLVED`` (an answer arrived via the 095 matcher),
# ``EPISODE_RECONCILE_REQUIRED`` (the kernel detected a lost pending channel on
# restart — the lease is gone by definition) and ``EPISODE_RECONCILE_RESOLVED``
# (a human/kernel decision to close or resume) are driven by something OTHER
# than the lease holder's progress, so the caller has no lease triple to
# present. Requiring fencing here would make ``mark_pending_channel_lost``
# uncallable: it runs precisely when the lease has expired on restart. The gate
# that remains is structural — these kinds stay in ``_EPISODE_LIFECYCLE_KINDS``,
# so a bare ``append_event`` still refuses them; only the structured command
# may write them.
_EPISODE_FENCED_KINDS = frozenset(
    {
        EventKind.EPISODE_STATUS_CHANGED,
        EventKind.EPISODE_YIELD_REQUESTED,
        EventKind.EPISODE_CHECKPOINT_COMMITTED,
        EventKind.EPISODE_CLOSED,
        EventKind.EPISODE_FAILED,
        EventKind.EPISODE_SUSPENDED,
        EventKind.EPISODE_ACTIVATED,
        EventKind.EPISODE_RECOVERING,
        EventKind.EPISODE_SIDE_EFFECT_RECORDED,
    }
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    provenance TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    work_item_id TEXT,
    task_id TEXT,
    episode_id TEXT,
    native_session_id TEXT,
    cause_id TEXT,
    correlation_id TEXT,
    outcome TEXT,
    payload TEXT NOT NULL,
    payload_hash TEXT,
    -- slice-087 M6: the ownership lease triple that authorised this write.
    -- NULL for non-fenced events (task.*/work_item.*/notes); for fenced
    -- Episode events it records WHICH lease/token committed the authoritative
    -- state change — durable audit (which grant wrote this?) and the basis for
    -- the full idempotent-retry fingerprint (codex H7).
    lease_id TEXT,
    owner TEXT,
    fencing_token INTEGER
);

CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    work_item_id TEXT,
    task_id TEXT,
    episode_id TEXT,
    cause_id TEXT,
    correlation_id TEXT,
    policy_version TEXT NOT NULL,
    signals TEXT NOT NULL,
    candidates TEXT NOT NULL,
    choice TEXT NOT NULL,
    reason TEXT NOT NULL,
    budget_before TEXT,
    budget_after TEXT
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    idempotency_key TEXT,
    released_at TEXT,
    -- slice-087: strictly monotonic per (resource_type, resource_id). The
    -- live counter lives in lease_fence_counters; this column caches the
    -- token the holder must present on fenced writes. DEFAULT 0 so an
    -- episode_ownership / work_lease that does not need fencing still works.
    fencing_token INTEGER NOT NULL DEFAULT 0
);

-- at most one ACTIVE lease per resource (CAS primitive)
CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_active
    ON leases(resource_type, resource_id) WHERE released_at IS NULL;

-- slice-087: idempotency is scoped to the RESOURCE, not global. codex review
-- of slice-087 caught that a global-unique key would let the same owner reuse
-- one key across an episode_ownership lease and a work_lease and silently get
-- back the wrong resource's lease. The partial index now requires
-- (resource_type, resource_id, idempotency_key) to be unique together.
CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_idem
    ON leases(resource_type, resource_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND released_at IS NULL;

-- slice-087: monotonic fencing counter per resource. Kept in its own table so
-- garbage-collecting old lease rows cannot roll the token back. Incremented
-- inside the same IMMEDIATE transaction that grants a new lease.
CREATE TABLE IF NOT EXISTS lease_fence_counters (
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    last_token INTEGER NOT NULL,
    PRIMARY KEY (resource_type, resource_id)
);

-- slice-087: Episode snapshot store. Each checkpoint is a new version row;
-- the reducer folds only the SnapshotRef (episode_id, version,
-- committed_event_id, payload_hash) into EpisodeState, never the payload.
-- checkpoint_key UNIQUE gives idempotent checkpoint on crash retry (version
-- alone cannot: a crash between COMMIT and response-return would else mint a
-- second version for the same checkpoint command). journal_through_seq pins
-- how far the journal was folded into this snapshot, so recovery_partial does
-- not re-fold events already represented in base_snapshot_ref.
CREATE TABLE IF NOT EXISTS episode_snapshots (
    episode_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    checkpoint_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    -- base_snapshot_ref split: a SnapshotRef is (episode_id, version, ...);
    -- the base's episode/version are stored flat for SQL.
    base_episode_id TEXT,
    base_version INTEGER,
    journal_through_seq INTEGER NOT NULL,
    committed_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (episode_id, version)
);

-- slice-086: foreground attention record. Single row (CHECK id=1); the
-- task_id is whoever Trowel is currently pushing forward, or NULL. Unlike
-- leases this row has NO expiry: foreground is "current fact", not a
-- resource that can be stolen by a timeout (Kleppmann: persistent lock vs
-- lease). Restart reads this row back as-is.
CREATE TABLE IF NOT EXISTS foreground_claim (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    task_id TEXT
);

-- slice-086: idempotency for Task creation. The CreateTaskFromUserRequest
-- command carries a caller-supplied key; a retry returns the original
-- task_id instead of producing a second Task + primary WorkItem. The CHECK
-- guards against NULL/blank keys at the storage layer too (SQLite accepts
-- NULL in a non-integer PRIMARY KEY, which would silently break the lookup).
CREATE TABLE IF NOT EXISTS task_create_keys (
    idempotency_key TEXT PRIMARY KEY NOT NULL
        CHECK (length(trim(idempotency_key)) > 0),
    task_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- slice-087: idempotency for Episode creation. start_episode carries a
-- caller-supplied key; a retry returns the original (episode_id, lease)
-- instead of producing a second Episode + lease. Same CHECK shape as
-- task_create_keys.
CREATE TABLE IF NOT EXISTS episode_create_keys (
    idempotency_key TEXT PRIMARY KEY NOT NULL
        CHECK (length(trim(idempotency_key)) > 0),
    episode_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class LeaseConflict(Exception):
    """Raised when a CAS lease claim loses to another active owner."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(
            f"lease already held: resource_type={resource_type} resource_id={resource_id}"
        )


class ForegroundConflict(Exception):
    """Raised when a ClaimForeground loses to another Task already foreground.

    The foreground claim is a single-row persistent record (no TTL); only one
    Task may hold it at a time. Idempotent re-claim by the same owner returns
    silently; a different owner raises this (slice-086 §foreground claim).
    """

    def __init__(self, current_owner: str | None) -> None:
        self.current_owner = current_owner
        super().__init__(
            f"foreground already held by task_id={current_owner!r}"
        )


class WarmFull(Exception):
    """Raised when a PromoteToWarm would exceed ``warm_limit``.

    Per slice-086 grill decision 7 (warm is a fixed-capacity cache, explicit
    replacement on overflow): the caller must demote an existing warm Task to
    backlog before promoting a new one. The exception carries the current
    warm Task ids so the caller / UI can surface the choice to the user.
    """

    def __init__(self, limit: int, warm_task_ids: tuple[str, ...]) -> None:
        self.limit = limit
        self.warm_task_ids = warm_task_ids
        super().__init__(
            f"warm pool full (limit={limit}); demote one of {warm_task_ids} first"
        )


class TaskCommandError(Exception):
    """Raised when a Task command violates an invariant: illegal transition,
    unknown task, terminal state, or a provenance/authority gate refusal
    (e.g. MODEL_HYPOTHESIS attempting to create a Task or confirm a user
    task's completion)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class EpisodeCommandError(Exception):
    """Raised when an Episode command violates an invariant: illegal
    transition, unknown episode, terminal state, ownership-token mismatch, or
    a checkpoint / snapshot contract violation (slice-087)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class StaleWriterRejected(Exception):
    """Raised when a fenced Episode write bears a stale ownership token.

    The writer's ``(lease_id, owner, fencing_token)`` did not match the live
    ownership lease: the lease was taken over (a higher token now exists),
    the caller is not the current owner, or the lease is expired / released.
    The attempted write is rejected and a ``late_write_rejected`` audit event
    is recorded. This is the concrete enforcement of the slice-087 fencing
    invariant (Kleppmann: storage-layer rejection of stale writers).

    NOT raised for the idempotent-retry case: if the exact same ``event_id``
    was already persisted, ``append_event`` returns the original seq without
    consulting fencing (a read-only retry that changed nothing).
    """

    def __init__(
        self,
        episode_id: str,
        reason: str,
        attempted_token: int | None = None,
        current_token: int | None = None,
    ) -> None:
        self.episode_id = episode_id
        self.reason = reason
        self.attempted_token = attempted_token
        self.current_token = current_token
        super().__init__(
            f"stale writer rejected for episode {episode_id!r}: {reason} "
            f"(attempted_token={attempted_token}, current_token={current_token})"
        )


# ----------------------------------------------------------------- helpers ---


def _now_iso() -> str:
    """Current UTC time as ISO-8601 (lexicographically sortable)."""

    return datetime.now(timezone.utc).isoformat()


def _payload_json(payload: dict[str, Any]) -> tuple[str, str]:
    """Redact, then serialise a payload and its short hash for audit."""

    redacted = redact_payload(payload)
    text = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return text, f"sha256:{digest}"


def _dumps(value: Any) -> str:
    """Redact then serialise any decision field (dict/list/str/None).

    Decisions carry free-form ``signals``/``candidates``/``reason``/
    ``budget_*``; they must NOT bypass redaction (spec: "默认日志不保存完整
    prompt、thinking 或私聊"). ``reason`` is a scalar — ``redact_payload``
    only scrubs it if the whole string matches a secret shape, so normal
    human-readable reason text survives intact.
    """

    return json.dumps(
        redact_payload(value), ensure_ascii=False, sort_keys=True, default=str
    )


# Shared INSERT statements + param builders. Kept module-level so every
# append path (event, decision, atomic pair) serialises the same way and
# applies the same redaction — no per-method copy can drift out of sync.

_EVENT_INSERT_SQL = (
    "INSERT INTO events (event_id, kind, occurred_at, source, provenance, "
    "policy_version, work_item_id, task_id, episode_id, native_session_id, "
    "cause_id, correlation_id, outcome, payload, payload_hash, "
    "lease_id, owner, fencing_token) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_DECISION_INSERT_SQL = (
    "INSERT INTO decisions (decision_id, kind, decided_at, work_item_id, "
    "task_id, episode_id, cause_id, correlation_id, policy_version, "
    "signals, candidates, choice, reason, budget_before, budget_after) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _event_params(
    event: EventEnvelope, payload_text: str, payload_hash: str
) -> tuple:
    """Build redacted INSERT params for an event row."""

    return (
        event.event_id,
        event.kind,
        event.occurred_at,
        event.source,
        event.provenance.value,
        event.policy_version,
        event.work_item_id,
        event.task_id,
        event.episode_id,
        event.native_session_id,
        event.cause_id,
        event.correlation_id,
        event.outcome,
        payload_text,
        payload_hash,
        # slice-087 M6: persist the lease triple (NULL for non-fenced events).
        event.lease_id,
        event.owner,
        event.fencing_token,
    )


def _event_identity(event: EventEnvelope, payload_hash: str) -> tuple:
    """The identity tuple of an event for idempotent-retry comparison.

    Two events sharing an ``event_id`` are the SAME event iff every field here
    matches. codex H7: the previous check compared only ``payload_hash``, so
    the same id + same payload but a different kind / entity ref / lease triple
    was silently treated as idempotent — the second event simply vanished.
    ``occurred_at`` is intentionally excluded: two retries at different wall-
    clock times are still the same logical event."""

    return (
        event.kind,
        event.source,
        event.provenance.value,
        event.policy_version,
        event.work_item_id,
        event.task_id,
        event.episode_id,
        event.native_session_id,
        event.cause_id,
        event.correlation_id,
        event.outcome,
        payload_hash,
        event.lease_id,
        event.owner,
        event.fencing_token,
    )


def _event_row_identity(row: sqlite3.Row, payload_hash: str) -> tuple:
    """Read the identity tuple back from a stored events row (``SELECT *``).

    Mirrors ``_event_identity`` so a stored row and a live envelope compare
    field-for-field. ``fencing_token`` is normalised NULL→None on both sides."""

    ft = row["fencing_token"]
    return (
        row["kind"],
        row["source"],
        row["provenance"],
        row["policy_version"],
        row["work_item_id"],
        row["task_id"],
        row["episode_id"],
        row["native_session_id"],
        row["cause_id"],
        row["correlation_id"],
        row["outcome"],
        row["payload_hash"],
        row["lease_id"],
        row["owner"],
        int(ft) if ft is not None else None,
    )


def _decision_params(decision: DecisionRecord) -> tuple:
    """Build redacted INSERT params for a decision row.

    ``signals``/``candidates``/``budget_*`` are JSON-serialised after
    redaction; ``reason`` is stored as a plain (redacted) string so the
    column stays human-readable; ``choice`` is a short structural value and
    is not redacted.
    """

    return (
        decision.decision_id,
        decision.kind,
        decision.decided_at,
        decision.work_item_id,
        decision.task_id,
        decision.episode_id,
        decision.cause_id,
        decision.correlation_id,
        decision.policy_version,
        _dumps(decision.signals),
        _dumps(decision.candidates),
        decision.choice,
        redact_payload(decision.reason),
        _dumps(decision.budget_before)
        if decision.budget_before is not None
        else None,
        _dumps(decision.budget_after)
        if decision.budget_after is not None
        else None,
    )


def _lease_from_row(row: sqlite3.Row) -> Lease:
    """Reconstruct a ``Lease`` from a leases table row."""

    return Lease(
        lease_id=row["lease_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        owner=row["owner"],
        acquired_at=row["acquired_at"],
        expires_at=row["expires_at"],
        idempotency_key=row["idempotency_key"],
        fencing_token=int(row["fencing_token"]),
    )


def _event_from_row(row: sqlite3.Row) -> EventEnvelope:
    """Reconstruct an ``EventEnvelope`` from an events table row.

    The stored payload is already redacted (the store redacts on insert), so
    no further scrubbing happens on read.
    """

    return EventEnvelope(
        event_id=row["event_id"],
        kind=row["kind"],
        occurred_at=row["occurred_at"],
        source=row["source"],
        provenance=Provenance(row["provenance"]),
        policy_version=row["policy_version"],
        payload=json.loads(row["payload"]),
        work_item_id=row["work_item_id"],
        task_id=row["task_id"],
        episode_id=row["episode_id"],
        native_session_id=row["native_session_id"],
        cause_id=row["cause_id"],
        correlation_id=row["correlation_id"],
        outcome=row["outcome"],
        # slice-087 M6: restore the persisted lease triple.
        lease_id=row["lease_id"],
        owner=row["owner"],
        fencing_token=int(row["fencing_token"])
        if row["fencing_token"] is not None
        else None,
    )


def _decision_from_row(row: sqlite3.Row) -> DecisionRecord:
    """Reconstruct a ``DecisionRecord`` from a decisions table row."""

    return DecisionRecord(
        decision_id=row["decision_id"],
        kind=row["kind"],
        decided_at=row["decided_at"],
        signals=json.loads(row["signals"]),
        candidates=json.loads(row["candidates"]),
        choice=row["choice"],
        reason=row["reason"],
        policy_version=row["policy_version"],
        budget_before=json.loads(row["budget_before"]) if row["budget_before"] else None,
        budget_after=json.loads(row["budget_after"]) if row["budget_after"] else None,
        work_item_id=row["work_item_id"],
        task_id=row["task_id"],
        episode_id=row["episode_id"],
        cause_id=row["cause_id"],
        correlation_id=row["correlation_id"],
    )


def _validate_work_item(kind: WorkItemKind, task_id: str | None) -> None:
    """Enforce the WorkItem structural invariant (spec interface contract).

    Task and incubation work must reference a Task; default/maintenance/
    experiment must NOT — they are system work and must never masquerade as
    task work.
    """

    if kind in (WorkItemKind.TASK, WorkItemKind.INCUBATION):
        if not task_id:
            raise ValueError(
                f"{kind.value} work item requires a task_id (got None)"
            )
    else:
        if task_id is not None:
            raise ValueError(
                f"{kind.value} work item must not reference a task "
                f"(got task_id={task_id!r})"
            )


# -------------------------------------------------- episode snapshot codecs ---
#
# slice-087: (de)serialise EpisodeSnapshot <-> JSON-safe dict for the
# ``episode_snapshots`` table. Kept at module level (like _lease_from_row /
# _event_from_row) because they are pure functions over value objects.


def _pending_to_payload(p: PendingDescriptor) -> dict[str, Any]:
    """Serialise a PendingDescriptor to a JSON-safe dict (event payload)."""

    return {
        "kind": p.kind.value,
        "native_generation": p.native_generation,
        "correlation_id": p.correlation_id,
        "cause": p.cause,
        "posed_at": p.posed_at,
    }


def _pending_from_payload(p: dict[str, Any]) -> PendingDescriptor:
    """Reconstruct a PendingDescriptor from its stored dict."""

    return PendingDescriptor(
        kind=WaitingSubtype(p["kind"]),
        native_generation=p.get("native_generation"),
        correlation_id=p["correlation_id"],
        cause=p.get("cause", ""),
        posed_at=p["posed_at"],
    )


def _snapshot_to_payload(s: EpisodeSnapshot) -> dict[str, Any]:
    """Serialise an EpisodeSnapshot to a JSON-safe dict for storage.

    Nested records (side effects, artifacts, pending, base ref) are
    flattened; the reverse is ``_snapshot_from_payload``.
    """

    return {
        "work_item_goal": s.work_item_goal,
        "task_constraints_ref": s.task_constraints_ref,
        "current_judgment": s.current_judgment,
        "completed_with_evidence": [list(pair) for pair in s.completed_with_evidence],
        "side_effects": [
            {
                "action_ref": se.action_ref,
                "idempotency_key": se.idempotency_key,
                "outcome": se.outcome,
                "evidence_ref": se.evidence_ref,
            }
            for se in s.side_effects
        ],
        "unknowns": list(s.unknowns),
        "waiting_condition": (
            _pending_to_payload(s.waiting_condition) if s.waiting_condition else None
        ),
        "next_steps": list(s.next_steps),
        "artifacts": [{"kind": a.kind, "ref": a.ref} for a in s.artifacts],
        "native_transcript_ref": s.native_transcript_ref,
        "source": s.source.value,
        "journal_through_seq": s.journal_through_seq,
        "base_snapshot_ref": (
            {
                "episode_id": s.base_snapshot_ref.episode_id,
                "version": s.base_snapshot_ref.version,
                "committed_event_id": s.base_snapshot_ref.committed_event_id,
                "payload_hash": s.base_snapshot_ref.payload_hash,
            }
            if s.base_snapshot_ref
            else None
        ),
    }


def _validate_episode_snapshot(
    snapshot: EpisodeSnapshot, payload_text: str
) -> None:
    """Boundary validation for a snapshot about to be persisted (codex M2).

    Spec §设计约束 mandates: a byte cap on the payload (line 224), ``next_steps``
    at most 3 (line 107), completed actions carry evidence (line 102), and a
    done side effect carries an evidence ref (line 170 / pass 7). The dataclass
    cannot enforce counts/emptiness, so the store checks at the write boundary.
    """

    if len(payload_text.encode("utf-8")) > _MAX_SNAPSHOT_PAYLOAD_BYTES:
        raise EpisodeCommandError(
            f"snapshot payload exceeds {_MAX_SNAPSHOT_PAYLOAD_BYTES} bytes "
            f"(got {len(payload_text.encode('utf-8'))}); reduce content or "
            f"reference instead of copying"
        )
    if len(snapshot.next_steps) > 3:
        raise EpisodeCommandError(
            f"next_steps must have at most 3 items (got {len(snapshot.next_steps)})"
        )
    for action_ref, evidence_ref in snapshot.completed_with_evidence:
        if not action_ref or not evidence_ref:
            raise EpisodeCommandError(
                "completed_with_evidence entries must be non-empty "
                "(action_ref, evidence_ref)"
            )
    for se in snapshot.side_effects:
        if se.outcome == "done" and not se.evidence_ref:
            raise EpisodeCommandError(
                f"side effect {se.action_ref!r} marked done without an "
                f"evidence_ref; record it unknown_requires_reconcile instead"
            )


def _snapshot_from_payload(p: dict[str, Any]) -> EpisodeSnapshot:
    """Reconstruct an EpisodeSnapshot from its stored payload dict."""

    waiting = p.get("waiting_condition")
    base = p.get("base_snapshot_ref")
    return EpisodeSnapshot(
        work_item_goal=p.get("work_item_goal", ""),
        task_constraints_ref=p.get("task_constraints_ref"),
        current_judgment=p.get("current_judgment", "unknown"),
        completed_with_evidence=tuple(
            tuple(pair) for pair in p.get("completed_with_evidence", [])
        ),
        side_effects=tuple(
            SideEffectRecord(
                action_ref=se["action_ref"],
                idempotency_key=se["idempotency_key"],
                outcome=se["outcome"],
                evidence_ref=se.get("evidence_ref"),
            )
            for se in p.get("side_effects", [])
        ),
        unknowns=tuple(p.get("unknowns", [])),
        waiting_condition=_pending_from_payload(waiting) if waiting else None,
        next_steps=tuple(p.get("next_steps", [])),
        artifacts=tuple(
            ArtifactRef(kind=a["kind"], ref=a["ref"]) for a in p.get("artifacts", [])
        ),
        native_transcript_ref=p.get("native_transcript_ref"),
        source=SnapshotSource(p.get("source", "cooperative")),
        journal_through_seq=int(p.get("journal_through_seq", 0)),
        base_snapshot_ref=(
            SnapshotRef(
                episode_id=base["episode_id"],
                version=int(base["version"]),
                committed_event_id=base["committed_event_id"],
                payload_hash=base["payload_hash"],
            )
            if base
            else None
        ),
    )


# ----------------------------------------------------------------- the store ---


class ModelOsStore:
    """Transactional SQLite store backing the Model OS journal."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        policy_version: str = _DEFAULT_POLICY_VERSION,
        warm_limit: int = 3,
    ) -> None:
        """Remember the db path and policy version; call ``open()`` to connect.

        Args:
            db_path: path to the model_os.db file (created on first open).
            policy_version: recorded on every event/decision so replay can
                explain why a new policy would decide differently.
            warm_limit: max number of warm Tasks (slice-086 grill decision 7;
                default 3, user-overridable policy). The foreground Task counts
                against this limit.
        """

        self._path = Path(db_path)
        self._policy_version = policy_version
        self._warm_limit = warm_limit
        self._conn: sqlite3.Connection | None = None
        # slice-086: serialise commands that share this connection. SQLite's
        # ``in_transaction`` is connection-scoped, not thread-scoped, so
        # without this lock two request handlers sharing one store would
        # interleave inside the same transaction (codex review HIGH 3).
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        """The backing db file path."""

        return self._path

    # --------------------------------------------------- lifecycle / bootstrap

    def open(self) -> None:
        """Open the connection and bootstrap the schema if absent."""

        self._conn = self._create_connection()
        self._bootstrap()

    def close(self) -> None:
        """Close the connection (idempotent)."""

        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _create_connection(self) -> sqlite3.Connection:
        """Create a WAL connection with the threading-safe flags set.

        ``check_same_thread=False`` is mandatory: later slices expose this
        store through FastAPI routes, whose TestClient runs async endpoints
        on an anyio portal thread (verified memory gotcha).

        ``isolation_level="IMMEDIATE"`` (slice-086): every ``with conn:``
        block opens an IMMEDIATE transaction, acquiring the reserved write
        lock up front so concurrent writers serialise rather than racing on
        a count-then-write window (the warm-pool capacity check relies on
        this). Lease CAS still works — the partial unique index is the
        arbiter, not the isolation level — so 084 behaviour is preserved.
        """

        conn = sqlite3.connect(str(self._path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = "IMMEDIATE"
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _bootstrap(self) -> None:
        """Create tables/indexes if missing and stamp the schema version.

        DDL runs via ``executescript`` (idempotent ``IF NOT EXISTS``); the
        schema-version stamp is a separate parameterised ``execute`` so the
        version never enters SQL via string substitution. Forward migrations
        (``_migrate_schema``) run after, for databases whose stamped version
        is behind ``_SCHEMA_VERSION`` — ``CREATE ... IF NOT EXISTS`` cannot
        add a column to an existing table or rebuild an index, so those land
        here as explicit ALTER / DROP + CREATE.
        """

        assert self._conn is not None
        # _bootstrap uses ``with self._conn`` (not ``_tx``) because
        # ``executescript`` manages its own transaction and would fight the
        # explicit BEGIN that ``_tx`` issues.
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)),
            )
            # slice-086: foreground_claim is a single-row table; seed row id=1
            # with NULL task_id (no foreground) so UPDATE-based CAS works and
            # restart always finds exactly one row to read.
            self._conn.execute(
                "INSERT OR IGNORE INTO foreground_claim (id, task_id) VALUES (1, NULL)"
            )
            self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Run forward migrations the executing engine is behind on.

        ``meta.schema_version`` is read AFTER ``executescript`` seeded it. On a
        fresh database the seed wrote the current ``_SCHEMA_VERSION`` and this
        is a no-op. On an older database the row already held a smaller
        version and ``CREATE ... IF NOT EXISTS`` left the old shapes in place,
        so the ALTER / DROP + CREATE work happens here.
        """

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        current = int(row["value"]) if row is not None else _SCHEMA_VERSION
        if current < 2:
            # v1 → v2 (slice-087): add leases.fencing_token and rebuild
            # idx_leases_idem as resource-scoped. CREATE ... IF NOT EXISTS in
            # _SCHEMA_SQL added the column for fresh databases; for old ones
            # the column must be ALTER-added. SQLite has no CREATE OR REPLACE
            # INDEX, so the renamed index is dropped and recreated.
            cols = [
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(leases)").fetchall()
            ]
            if "fencing_token" not in cols:
                self._conn.execute(
                    "ALTER TABLE leases ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0"
                )
            self._conn.execute("DROP INDEX IF EXISTS idx_leases_idem")
            self._conn.execute(
                "CREATE UNIQUE INDEX idx_leases_idem "
                "ON leases(resource_type, resource_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
            # stamp INSIDE the branch. Setting ``current = 2`` and then testing
            # ``current != _SCHEMA_VERSION`` would be False (2 == 2), silently
            # no-op the stamp, and every reopen would re-migrate forever.
            self._conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                ("2",),
            )
            current = 2
        if current < 3:
            # v2 → v3 (slice-087 M6): persist the fenced-event lease triple on
            # the events row. CREATE ... IF NOT EXISTS added the columns for
            # fresh databases; old v2 databases need ALTER ADD COLUMN. All
            # three are nullable (NULL for non-fenced events).
            ev_cols = [
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(events)").fetchall()
            ]
            for col in ("lease_id", "owner", "fencing_token"):
                if col not in ev_cols:
                    self._conn.execute(
                        f"ALTER TABLE events ADD COLUMN {col} "
                        f"{'INTEGER' if col == 'fencing_token' else 'TEXT'}"
                    )
            self._conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                ("3",),
            )
            current = 3
        if current < 4:
            # v3 → v4 (slice-087 R3-H3): scope idx_leases_idem to
            # ``released_at IS NULL`` so a released lease's idempotency_key does
            # not block a new grant (the takeover redesign marks the old row
            # released + INSERTs a fresh row, preserving history). The old
            # index spanned released rows too, so reusing a key across a
            # takeover raised a raw sqlite3.IntegrityError that leaked past the
            # command layer. SQLite has no CREATE OR REPLACE INDEX.
            self._conn.execute("DROP INDEX IF EXISTS idx_leases_idem")
            self._conn.execute(
                "CREATE UNIQUE INDEX idx_leases_idem "
                "ON leases(resource_type, resource_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL AND released_at IS NULL"
            )
            self._conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                ("4",),
            )

    def _schema_version(self) -> int:
        """Return the schema version stamped in ``meta``."""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row is not None else _SCHEMA_VERSION

    # ------------------------------------------------------------ work items

    def create_work_item(
        self,
        *,
        kind: WorkItemKind,
        owner_ref: str,
        task_id: str | None,
        session_purpose: SessionPurpose,
        memory_eligibility: MemoryEligibility,
    ) -> WorkItem:
        """Create a WorkItem of any legal kind and journal it.

        Validates the kind/task_id invariant, then appends a
        ``work_item.created`` event. The WorkItem starts PENDING; later
        slices drive its lifecycle.

        slice-086: ``kind=TASK`` is refused here. A Task's primary WorkItem
        must come from ``create_task_from_user_request`` (which creates both
        atomically and enforces the 1:1 mapping). Allowing ``create_work_item``
        to mint TASK WorkItems would let a caller give one Task multiple
        primary WorkItems, or attach one to a non-existent task_id (codex
        review HIGH 4).
        """

        if kind == WorkItemKind.TASK:
            raise TaskCommandError(
                "TASK WorkItems must be created via "
                "create_task_from_user_request (slice-086: Task↔primary "
                "WorkItem is 1:1)"
            )
        _validate_work_item(kind, task_id)
        work_item_id = uuid4().hex
        created_at = _now_iso()
        work_item = WorkItem(
            work_item_id=work_item_id,
            kind=kind,
            owner_ref=owner_ref,
            task_id=task_id,
            status=WorkItemStatus.PENDING,
            session_purpose=session_purpose,
            memory_eligibility=memory_eligibility,
            created_at=created_at,
        )
        event = EventEnvelope(
            event_id=f"wi.create.{work_item_id}",
            kind=EventKind.WORK_ITEM_CREATED,
            occurred_at=created_at,
            source="kernel",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload={
                "work_item_id": work_item_id,
                "kind": kind.value,
                "owner_ref": owner_ref,
                "task_id": task_id,
                "status": WorkItemStatus.PENDING.value,
                "session_purpose": session_purpose.value,
                "memory_eligibility": memory_eligibility.value,
            },
            work_item_id=work_item_id,
            task_id=task_id,
        )
        self.append_event(event)
        return work_item

    # ------------------------------------------------------------ lease / CAS

    def acquire_lease(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None = None,
    ) -> Lease:
        """Atomically claim a lease (compare-and-set) with a fencing token.

        Returns the held lease on success. Raises ``LeaseConflict`` if another
        active, unexpired lease already owns the resource. An expired lease is
        taken over atomically. Re-claiming with the same ``idempotency_key``
        AND the same owner returns the original lease; the same key under a
        different owner, or the same key already used on a DIFFERENT
        resource, is a conflict — the key is the caller's retry identity for
        ONE resource, not a transferable handle (slice-087 codex review: the
        v1 global-unique index let one key silently cross resources).

        slice-087: every grant mints a strictly-higher fencing token for the
        ``(resource_type, resource_id)`` pair (from ``lease_fence_counters``),
        so a stale holder that wakes up after takeover cannot pass its old
        token past a fenced write.
        """

        assert self._conn is not None
        now_str = _now_iso()
        expires_str = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()

        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type=? AND resource_id=? "
                "AND idempotency_key=? AND released_at IS NULL",
                (resource_type, resource_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise LeaseConflict(resource_type, resource_id)
                # codex R3-M3: do not return an already-expired lease — a retry
                # that lands after TTL is a conflict; the caller must re-acquire
                # fresh (otherwise the first fenced write fails on expiry).
                if existing["expires_at"] <= now_str:
                    raise LeaseConflict(resource_type, resource_id)
                return _lease_from_row(existing)

        try:
            with self._tx():
                token = self._next_fence_token_in_tx(resource_type, resource_id)
                lease_id = uuid4().hex
                self._conn.execute(
                    "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                    "acquired_at, expires_at, idempotency_key, released_at, "
                    "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    (
                        lease_id,
                        resource_type,
                        resource_id,
                        owner,
                        now_str,
                        expires_str,
                        idempotency_key,
                        token,
                    ),
                )
                return Lease(
                    lease_id=lease_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    owner=owner,
                    acquired_at=now_str,
                    expires_at=expires_str,
                    idempotency_key=idempotency_key,
                    fencing_token=token,
                )
        except sqlite3.IntegrityError:
            return self._takeover_or_conflict(
                resource_type,
                resource_id,
                owner,
                now_str,
                expires_str,
                idempotency_key,
            )

    def _next_fence_token_in_tx(
        self, resource_type: str, resource_id: str
    ) -> int:
        """Return the next fencing token for a resource, incrementing the
        counter inside the caller's open transaction.

        ``lease_fence_counters`` lives outside the ``leases`` rows so that
        releasing or garbage-collecting old lease rows never rolls the token
        back. The UPSERT and the lease INSERT share one IMMEDIATE
        transaction, so concurrent grants serialise and each gets a distinct
        token. A grant that fails (lease INSERT IntegrityError) rolls the
        whole transaction back, including this increment — no token is wasted.
        """

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT last_token FROM lease_fence_counters "
            "WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        ).fetchone()
        new_token = (int(row["last_token"]) + 1) if row is not None else 1
        self._conn.execute(
            "INSERT INTO lease_fence_counters (resource_type, resource_id, last_token) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(resource_type, resource_id) "
            "DO UPDATE SET last_token=excluded.last_token",
            (resource_type, resource_id, new_token),
        )
        return new_token

    def _takeover_or_conflict(
        self,
        resource_type: str,
        resource_id: str,
        owner: str,
        now_str: str,
        expires_str: str,
        idempotency_key: str | None,
    ) -> Lease:
        """Handle an INSERT conflict: reclaim by idempotency, take over an
        expired lease, or raise ``LeaseConflict``.

        slice-087: takeover no longer UPDATEs the old row in place. It marks
        the expired row released (so the old grant's audit trail and its
        idempotency key are retained) and INSERTs a fresh lease row with a
        new, higher fencing token. An in-place UPDATE would lose the old
        grant's history, drop the old key, and could not hand the new owner a
        higher token than the old one.
        """

        assert self._conn is not None
        if idempotency_key is not None:
            row = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type=? AND resource_id=? "
                "AND idempotency_key=? AND released_at IS NULL",
                (resource_type, resource_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row["owner"] != owner:
                    raise LeaseConflict(resource_type, resource_id)
                return _lease_from_row(row)
        existing = self._conn.execute(
            "SELECT * FROM leases WHERE resource_type=? AND resource_id=? "
            "AND released_at IS NULL",
            (resource_type, resource_id),
        ).fetchone()
        if existing is not None and existing["expires_at"] <= now_str:
            with self._tx():
                # Mark the expired grant released, then INSERT a fresh row.
                # The partial unique index idx_leases_active (WHERE
                # released_at IS NULL) frees up exactly when the UPDATE
                # commits, so the INSERT cannot collide with the old row.
                # codex L2: the boundary is ``<=`` (not ``<``) to match the
                # fencing check ``expires_at <= now`` — at the exact expiry
                # tick the old owner has already lost power, so the new owner
                # must take over immediately with no unwritable gap.
                cur = self._conn.execute(
                    "UPDATE leases SET released_at=? "
                    "WHERE resource_type=? AND resource_id=? AND released_at IS NULL "
                    "AND expires_at <= ?",
                    (now_str, resource_type, resource_id, now_str),
                )
                if cur.rowcount == 1:
                    token = self._next_fence_token_in_tx(resource_type, resource_id)
                    new_lease_id = uuid4().hex
                    try:
                        self._conn.execute(
                            "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                            "acquired_at, expires_at, idempotency_key, released_at, "
                            "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                            (
                                new_lease_id,
                                resource_type,
                                resource_id,
                                owner,
                                now_str,
                                expires_str,
                                idempotency_key,
                                token,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        # codex R3-H3: a collision here is a key-scope or
                        # active-lease conflict (another writer raced us, or the
                        # idempotency_key is still in use). Surface it as a
                        # LeaseConflict, never a raw sqlite3 error.
                        raise LeaseConflict(resource_type, resource_id) from exc
                    return Lease(
                        lease_id=new_lease_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        owner=owner,
                        acquired_at=now_str,
                        expires_at=expires_str,
                        idempotency_key=idempotency_key,
                        fencing_token=token,
                    )
        raise LeaseConflict(resource_type, resource_id)

    def release_lease(self, lease_id: str) -> bool:
        """Release a lease by id. Returns ``True`` if it was active."""

        assert self._conn is not None
        with self._tx():
            cur = self._conn.execute(
                "UPDATE leases SET released_at=? WHERE lease_id=? AND released_at IS NULL",
                (_now_iso(), lease_id),
            )
            return cur.rowcount == 1

    def _read_active_leases(self) -> tuple[Lease, ...]:
        """Return all currently-active (unreleased, unexpired) leases."""

        assert self._conn is not None
        now_str = _now_iso()
        rows = self._conn.execute(
            "SELECT * FROM leases WHERE released_at IS NULL AND expires_at > ? "
            "ORDER BY acquired_at",
            (now_str,),
        ).fetchall()
        return tuple(_lease_from_row(r) for r in rows)

    # --------------------------------------------------------- append events

    def append_event(self, event: EventEnvelope) -> int:
        """Append an event (idempotent on ``event_id``); return its seq.

        The payload is redacted before it touches SQLite. Re-appending the
        same ``event_id`` with the SAME content returns the original seq
        (idempotent replay); re-appending the same ``event_id`` with DIFFERENT
        content raises ``ValueError`` — the caller reused an id across
        distinct events, which must not silently alias them (slice-087 codex
        review: the v1 path swallowed every duplicate as idempotent).

        slice-086 / slice-087: Task and Episode lifecycle kinds are refused —
        those must go through the structured commands so the gates cannot be
        bypassed by a caller that happens to hold a Store handle.
        """

        assert self._conn is not None
        if event.kind in _TASK_LIFECYCLE_KINDS:
            raise TaskCommandError(
                f"event kind {event.kind!r} is a Task lifecycle event; use "
                f"the corresponding structured command (provenance is not an "
                f"authorisation mechanism)"
            )
        if event.kind in _EPISODE_LIFECYCLE_KINDS:
            raise EpisodeCommandError(
                f"event kind {event.kind!r} is an Episode lifecycle event; "
                f"use the corresponding structured command (slice-087)"
            )
        payload_text, payload_hash = _payload_json(event.payload)
        try:
            with self._tx():
                self._conn.execute(
                    _EVENT_INSERT_SQL,
                    _event_params(event, payload_text, payload_hash),
                )
        except sqlite3.IntegrityError:
            # duplicate event_id: idempotent only if the FULL identity matches.
            # codex H7: comparing payload_hash alone let a reused id with a
            # different kind / entity / lease triple silently alias to the
            # first event. Compare every identity field now.
            existing = self._conn.execute(
                "SELECT * FROM events WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            if existing is None or _event_row_identity(
                existing, payload_hash
            ) != _event_identity(event, payload_hash):
                raise ValueError(
                    f"event_id {event.event_id!r} already exists with "
                    f"different content (identity mismatch)"
                )
        row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    # -------------------------------------------------------- slice-088 context
    #
    # Thin, idempotent append wrappers for context observations. The event
    # kinds are NOT in _TASK_LIFECYCLE_KINDS / _EPISODE_LIFECYCLE_KINDS, so
    # they go through the public append_event path (no structured-command gate
    # needed — a context sample is a passive observation, not a lifecycle move).
    #
    # Idempotency (A4): a sample's event_id embeds the runtime event's stable
    # ``occurred_at`` (NOT the write wall-clock), so a crash-retry that re-sends
    # the SAME observation is idempotent, while an evolving usage (subagent same
    # message.id with growing usage) lands as a DISTINCT event and the reducer
    # keeps the last — mirroring 082's "keep last" dedup online.

    def record_context_sample(
        self,
        sample: ContextSample,
        *,
        episode_id: str | None,
        occurred_at: str,
        task_id: str | None = None,
        source: str = "observer",
    ) -> int:
        """Append one CONTEXT_SAMPLE_OBSERVED event; idempotent on identity.

        Args:
            sample: the observation (used/window/ratio/confidence/...). Its
                ``native_session_id`` / ``request_identity`` / ``generation``
                feed the event_id so retries of the same observation collide
                idempotently.
            episode_id: the Episode this observation belongs to (envelope-level
                owner; the single source of truth — extra-HIGH 2).
            occurred_at: the RUNTIME event's timestamp (stable across retries),
                not the write wall-clock — this is what makes retry idempotent.
            task_id: optional Task pointer.
            source: provenance tag (default "observer").
        """

        # codex review HIGH 3: the event_id must NOT embed occurred_at — the
        # AgentEvent stream carries no stable event time, so a write-clock time
        # would make crash-replay non-idempotent. Instead, identity is
        # (episode, native_session, request, generation). Evolving usage (same
        # message.id streaming {0,0}→real) is collapsed by the batch
        # calculator's "keep last" dedup, so one request → one sample → one
        # stable id; replaying the same observation is idempotent.
        event_id = (
            f"ctx.sample.{episode_id or 'no-ep'}.{sample.native_session_id}."
            f"{sample.request_identity}.{sample.generation}"
        )
        event = EventEnvelope(
            event_id=event_id,
            kind=EventKind.CONTEXT_SAMPLE_OBSERVED,
            occurred_at=occurred_at,
            source=source,
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload=context_sample_to_dict(sample),
            task_id=task_id,
            episode_id=episode_id,
            native_session_id=sample.native_session_id,
        )
        return self.append_event(event)

    def record_context_boundary(
        self,
        native_session_id: str,
        *,
        episode_id: str | None,
        generation: int,
        occurred_at: str,
        trigger: str | None = None,
        task_id: str | None = None,
        source: str = "observer",
    ) -> int:
        """Append one CONTEXT_GENERATION_BOUNDARY event (audit-only in reducer).

        Per 083: the boundary event MUST land BEFORE the post-boundary samples,
        so a replay rebuilds the same generation sequence. ``generation`` is
        carried for audit; the derived generation on a ContextSample comes from
        the calculator, not from this event.
        """

        event_id = (
            f"ctx.boundary.{episode_id or 'no-ep'}.{native_session_id}.{generation}"
        )
        event = EventEnvelope(
            event_id=event_id,
            kind=EventKind.CONTEXT_GENERATION_BOUNDARY,
            occurred_at=occurred_at,
            source=source,
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload={"generation": generation, "trigger": trigger},
            task_id=task_id,
            episode_id=episode_id,
            native_session_id=native_session_id,
        )
        return self.append_event(event)

    def append_decision(self, decision: DecisionRecord) -> int:
        """Append a decision (idempotent on ``decision_id``); return its seq.

        ``signals``/``candidates``/``reason``/``budget_*`` are redacted before
        persisting — decisions must not bypass redaction (spec: no full
        prompt/thinking/private chat in the log).
        """

        assert self._conn is not None
        try:
            with self._tx():
                self._conn.execute(_DECISION_INSERT_SQL, _decision_params(decision))
        except sqlite3.IntegrityError:
            pass
        row = self._conn.execute(
            "SELECT seq FROM decisions WHERE decision_id=?",
            (decision.decision_id,),
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    def append_decision_with_intent(
        self, decision: DecisionRecord, intent_event: EventEnvelope
    ) -> tuple[int, int]:
        """Atomically append a decision and its triggering intent event.

        Honours the spec ordering "先记录决定，再执行命令，再记录 result": the
        decision and the intent event land in one transaction, so a crash
        leaves neither (no half decision, no duplicated seq).

        Idempotent on retry: if BOTH ``decision_id`` and ``event_id`` already
        exist, the call returns their original seqs. If only one exists the
        pair was split (crash mid-commit, or the caller reused an id from
        another context) — that partial state is surfaced, not swallowed.
        """

        assert self._conn is not None
        # codex N1 (round 2): this public entry point must apply the SAME
        # lifecycle gate as ``append_event``. Without it a caller could forge
        # an ``episode.status_changed`` / ``task.created`` intent here and
        # bypass every structured-command + fencing gate — the journal would
        # fold the forged event into authoritative state on the next replay.
        if intent_event.kind in _TASK_LIFECYCLE_KINDS:
            raise TaskCommandError(
                f"intent event kind {intent_event.kind!r} is a Task lifecycle "
                f"event; use the corresponding structured command"
            )
        if intent_event.kind in _EPISODE_LIFECYCLE_KINDS:
            raise EpisodeCommandError(
                f"intent event kind {intent_event.kind!r} is an Episode "
                f"lifecycle event; use the corresponding structured command "
                f"(slice-087)"
            )
        payload_text, payload_hash = _payload_json(intent_event.payload)
        try:
            with self._tx():
                self._conn.execute(_DECISION_INSERT_SQL, _decision_params(decision))
                self._conn.execute(
                    _EVENT_INSERT_SQL,
                    _event_params(intent_event, payload_text, payload_hash),
                )
        except sqlite3.IntegrityError:
            if not self._pair_already_present(
                decision.decision_id, intent_event.event_id
            ):
                raise
        d_row = self._conn.execute(
            "SELECT seq FROM decisions WHERE decision_id=?",
            (decision.decision_id,),
        ).fetchone()
        e_row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (intent_event.event_id,)
        ).fetchone()
        assert d_row is not None and e_row is not None
        return int(d_row["seq"]), int(e_row["seq"])

    def _pair_already_present(
        self, decision_id: str, event_id: str
    ) -> bool:
        """True only when BOTH ids already exist (an idempotent retry)."""

        assert self._conn is not None
        d = self._conn.execute(
            "SELECT 1 FROM decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        e = self._conn.execute(
            "SELECT 1 FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return d is not None and e is not None

    # ------------------------------------------------------------- read API

    def list_events(self, from_seq: int = 0) -> list[tuple[int, EventEnvelope]]:
        """Return ``(seq, event)`` pairs with ``seq > from_seq`` in order."""

        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM events WHERE seq > ? ORDER BY seq",
            (from_seq,),
        ).fetchall()
        return [(int(row["seq"]), _event_from_row(row)) for row in rows]

    def list_decisions(self, from_seq: int = 0) -> list[tuple[int, DecisionRecord]]:
        """Return ``(seq, decision)`` pairs with ``seq > from_seq`` in order."""

        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE seq > ? ORDER BY seq",
            (from_seq,),
        ).fetchall()
        return [(int(row["seq"]), _decision_from_row(row)) for row in rows]

    def replay(self, from_seq: int = 0) -> Snapshot:
        """Replay the event log past ``from_seq`` and return the derived
        snapshot.

        ``from_seq`` is the last seq already folded (default 0 → full
        replay). ``active_leases`` is intentionally empty here — it is live
        table state; ``read_snapshot`` fills it.
        """

        snap = initial_snapshot(schema_version=self._schema_version())
        for seq, event in self.list_events(from_seq=from_seq):
            snap = reduce_event(snap, event)
            snap = replace(snap, last_seq=seq)
        return snap

    # ---------------------------------------------------------- task commands
    #
    # slice-086 structured command entry points for the Task pool. Each
    # command runs in a single IMMEDIATE transaction: replay current state,
    # validate, append journal events, and (where relevant) mutate the
    # foreground_claim table — all atomic. Provenance is NOT the gate (grill
    # decision 5): the command identity is. MODEL_HYPOTHESIS never reaches
    # create_task; user-task completion requires USER_DECISION.
    #
    # Retry semantics: event_ids are random uuids, so a crash-mid-commit
    # retry may append duplicate status events. The reducer is idempotent for
    # these (setting READY twice leaves the Task READY), so derived state is
    # correct; only the audit log carries the duplicate. Task creation itself
    # is fully idempotent via task_create_keys.

    @contextmanager
    def _tx(self):
        """IMMEDIATE transaction that puts the replay SELECT inside the snapshot.

        ``isolation_level="IMMEDIATE"`` only auto-BEGINs before DML, so a
        ``replay()`` (SELECT) at the top of a command reads in autocommit mode
        — its count can be stale by the time the writes BEGIN, opening a
        count-then-write race (the warm-pool overflow that this slice's
        concurrent-promote test catches). This helper BEGINs explicitly so the
        read and the write share one snapshot; IMMEDIATE also serialises
        writers on the reserved lock.

        The ``RLock`` serialises commands that share this connection: SQLite's
        ``in_transaction`` is connection-scoped not thread-scoped, so without
        it two request handlers sharing one store would interleave inside the
        same transaction (codex review HIGH 3). Same-thread re-entry (a command
        calling a helper that also opens ``_tx``) is allowed by the RLock and
        short-circuits via the ``in_transaction`` check.
        """

        assert self._conn is not None
        with self._lock:
            if self._conn.in_transaction:
                yield
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.execute("COMMIT")
            except BaseException as exc:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                # codex M1: a StaleWriterRejected must still leave a DURABLE
                # ``late_write_rejected`` audit trail. The fenced write was just
                # rolled back above; record the audit in its OWN transaction so
                # it survives. The attempted event is attached by
                # ``_append_fenced_event_in_tx``. Best-effort: an audit failure
                # must never mask the original rejection.
                if isinstance(exc, StaleWriterRejected):
                    attempted = getattr(exc, "attempted_event", None)
                    if attempted is not None:
                        try:
                            self._conn.execute("BEGIN IMMEDIATE")
                            self._reject_stale_write_in_tx(attempted, exc)
                            self._conn.execute("COMMIT")
                        except BaseException:
                            # codex R3-M7: never silently swallow — log so an
                            # audit-store failure is observable. The original
                            # StaleWriterRejected is still re-raised below.
                            _LOGGER.warning(
                                "late_write_rejected audit failed; the "
                                "original StaleWriterRejected is preserved",
                                exc_info=True,
                            )
                            if self._conn.in_transaction:
                                self._conn.execute("ROLLBACK")
                raise

    @contextmanager
    def _read_tx(self):
        """Read transaction: BEGIN DEFERRED so replay + lease + foreground reads
        share one snapshot (codex review HIGH 5).

        Without this, ``read_snapshot``'s three SELECTs run in autocommit and
        a concurrent claim/release commit between them can return inconsistent
        state (e.g. Task=RUNNING but foreground_task_id=None). DEFERRED takes
        only a shared lock — WAL writers are not blocked — but the three reads
        cannot be split by a commit.
        """

        assert self._conn is not None
        with self._lock:
            if self._conn.in_transaction:
                yield
                return
            self._conn.execute("BEGIN")
            try:
                yield
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _read_foreground_task_id(self) -> str | None:
        """Return the current foreground task_id, or None (no foreground)."""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT task_id FROM foreground_claim WHERE id=1"
        ).fetchone()
        return None if row is None else row["task_id"]

    def read_foreground_task_id(self) -> str | None:
        """Public read of the current foreground task_id (or None).

        slice-086 references this as the public check (e.g. before demoting a
        Task). The private ``_read_foreground_task_id`` is the in-tx helper;
        this wrapper is the outside-tx read for callers/tests that do not hold
        a transaction."""

        return self._read_foreground_task_id()

    def _insert_event_in_tx(self, event: EventEnvelope) -> int | None:
        """Append an event inside the caller's open transaction.

        Returns the assigned seq, or ``None`` on duplicate event_id (idempotent
        skip). The caller already holds ``with self._tx():``, so this MUST NOT
        open its own transaction — a nested ``with conn`` would commit the
        outer work prematurely.
        """

        assert self._conn is not None
        payload_text, payload_hash = _payload_json(event.payload)
        try:
            self._conn.execute(
                _EVENT_INSERT_SQL,
                _event_params(event, payload_text, payload_hash),
            )
        except sqlite3.IntegrityError:
            # codex H7: duplicate event_id is idempotent ONLY if the full
            # identity matches; a reused id on a different event must surface
            # rather than vanish.
            existing = self._conn.execute(
                "SELECT * FROM events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if existing is None or _event_row_identity(
                existing, payload_hash
            ) != _event_identity(event, payload_hash):
                raise ValueError(
                    f"event_id {event.event_id!r} already exists with "
                    f"different content (identity mismatch)"
                )
            return None  # idempotent duplicate
        row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    def _require_task(self, snap: Snapshot, task_id: str) -> TaskState:
        """Return the TaskState for task_id or raise TaskCommandError."""

        task = next((t for t in snap.tasks if t.task_id == task_id), None)
        if task is None:
            raise TaskCommandError(f"unknown task_id={task_id!r}")
        return task

    def _require_non_terminal(self, task: TaskState) -> None:
        """Raise TaskCommandError if the Task is already terminal."""

        if task.status.is_terminal:
            raise TaskCommandError(
                f"task {task.task_id!r} is terminal ({task.status.value})"
            )

    def _require_status(
        self, task: TaskState, allowed: set[TaskStatus]
    ) -> None:
        """Raise TaskCommandError unless the Task is in one of ``allowed``
        source states (codex review HIGH 2). Commands check ``non_terminal``
        first, then this — so the frozen state graph (backlog→ready→running,
        running→{waiting,done,...}) is enforced, not just "not terminal"."""

        if task.status not in allowed:
            allowed_str = sorted(s.value for s in allowed)
            raise TaskCommandError(
                f"task {task.task_id!r} status {task.status.value} not in "
                f"allowed source states {allowed_str}"
            )

    @staticmethod
    def _task_state_to_task(state: TaskState) -> Task:
        """Project a reducer TaskState into the public Task value object
        (drops ``status_provenance``, which is audit-only)."""

        return Task(
            task_id=state.task_id,
            origin=state.origin,
            original_goal=state.original_goal,
            appended_constraints=state.appended_constraints,
            status=state.status,
            priority=state.priority,
            warm=state.warm,
            warm_rank=state.warm_rank,
            authorization_scope=state.authorization_scope,
            waiting_condition=state.waiting_condition,
            completion_evidence=state.completion_evidence,
            error_record=state.error_record,
            primary_work_item_id=state.primary_work_item_id,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    def _make_task_event(
        self,
        kind: str,
        task_id: str,
        payload: dict[str, Any],
        provenance: Provenance = Provenance.MACHINE_OBSERVATION,
        work_item_id: str | None = None,
    ) -> EventEnvelope:
        """Build a kernel-originated task event with a fresh event_id."""

        return EventEnvelope(
            event_id=f"{kind}.{uuid4().hex}",
            kind=kind,
            occurred_at=_now_iso(),
            source="kernel",
            provenance=provenance,
            policy_version=self._policy_version,
            payload=payload,
            task_id=task_id,
            work_item_id=work_item_id,
        )

    def _work_item_status_event(
        self,
        work_item_id: str,
        new_status: WorkItemStatus,
        task_id: str | None,
        now: str,
    ) -> EventEnvelope:
        """Build a work_item.status_changed event that keeps the primary
        WorkItem in lockstep with its Task (slice-086 §映射表)."""

        return EventEnvelope(
            event_id=f"wi.status.{work_item_id}.{uuid4().hex}",
            kind=EventKind.WORK_ITEM_STATUS_CHANGED,
            occurred_at=now,
            source="kernel",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload={"new_status": new_status.value},
            work_item_id=work_item_id,
            task_id=task_id,
        )

    def _release_foreground_in_tx(self, task_id: str) -> None:
        """Clear foreground_claim (if held by ``task_id``) + emit
        FOREGROUND_RELEASED audit event.

        Caller holds the transaction. CAS: the UPDATE only fires when the
        current foreground IS ``task_id``, and the release event is emitted
        only when a row was actually cleared. slice-087 codex review caught
        that the old unconditional ``WHERE id=1`` UPDATE would silently clear
        ANOTHER task's claim the moment this helper is reused by compound
        Episode commands (suspend) that pass a task_id without pre-checking.
        Existing 086 callers pre-check ``read_foreground_task_id() == task_id``
        first, so this CAS is transparent for them and defensive for 087.
        """

        assert self._conn is not None
        cur = self._conn.execute(
            "UPDATE foreground_claim SET task_id=NULL WHERE id=1 AND task_id=?",
            (task_id,),
        )
        if cur.rowcount == 1:
            self._insert_event_in_tx(
                self._make_task_event(EventKind.FOREGROUND_RELEASED, task_id, {})
            )

    # ------------------------------------------------------------- creation

    def create_task_from_user_request(
        self,
        *,
        original_goal: str,
        idempotency_key: str,
        authorization_scope: str = "",
        priority: int = 0,
    ) -> Task:
        """Create a Task + its primary WorkItem atomically and idempotently.

        ``provenance=USER_DECISION`` is written by this trusted boundary;
        callers cannot forge it. Retrying the same ``idempotency_key`` returns
        the original Task without creating a second primary WorkItem (pass 7).
        A retry that passes different ``original_goal`` /
        ``authorization_scope`` / ``priority`` ignores those fields — the first
        write is authoritative (idempotent retry is crash recovery, not
        update; to revise a Task use ``append_constraint`` /
        ``change_authorization``).
        """

        assert self._conn is not None
        if not original_goal:
            raise TaskCommandError("original_goal must be non-empty")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            # SQLite accepts NULL in a non-integer PRIMARY KEY, and
            # ``WHERE idempotency_key = NULL`` never matches — so a None/blank
            # key would defeat the idempotency check and let retries create
            # duplicate Tasks (codex review HIGH 6).
            raise TaskCommandError("idempotency_key must be a non-empty string")
        with self._tx():
            existing = self._conn.execute(
                "SELECT task_id FROM task_create_keys WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                snap_pre = self.replay()
                return self._task_state_to_task(
                    self._require_task(snap_pre, existing["task_id"])
                )

            task_id = uuid4().hex
            work_item_id = uuid4().hex
            now = _now_iso()
            self._insert_event_in_tx(
                EventEnvelope(
                    event_id=f"wi.create.{work_item_id}",
                    kind=EventKind.WORK_ITEM_CREATED,
                    occurred_at=now,
                    source="kernel",
                    provenance=Provenance.MACHINE_OBSERVATION,
                    policy_version=self._policy_version,
                    payload={
                        "work_item_id": work_item_id,
                        "kind": WorkItemKind.TASK.value,
                        "owner_ref": "user",
                        "task_id": task_id,
                        "status": WorkItemStatus.PENDING.value,
                        "session_purpose": SessionPurpose.FOREGROUND.value,
                        "memory_eligibility": MemoryEligibility.ELIGIBLE.value,
                    },
                    work_item_id=work_item_id,
                    task_id=task_id,
                )
            )
            self._insert_event_in_tx(
                EventEnvelope(
                    event_id=f"task.create.{task_id}",
                    kind=EventKind.TASK_CREATED,
                    occurred_at=now,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=self._policy_version,
                    payload={
                        "task_id": task_id,
                        "origin": TaskOrigin.USER_REQUEST.value,
                        "original_goal": original_goal,
                        "appended_constraints": [],
                        "status": TaskStatus.BACKLOG.value,
                        "priority": priority,
                        "warm": False,
                        "warm_rank": None,
                        "authorization_scope": authorization_scope,
                        "primary_work_item_id": work_item_id,
                    },
                    task_id=task_id,
                )
            )
            self._conn.execute(
                "INSERT INTO task_create_keys (idempotency_key, task_id, created_at) "
                "VALUES (?, ?, ?)",
                (idempotency_key, task_id, now),
            )
        snap = self.replay()
        return self._task_state_to_task(self._require_task(snap, task_id))

    # ----------------------------------------------------- warm / foreground

    def promote_to_warm(self, task_id: str) -> None:
        """backlog → warm ready. Raises WarmFull if warm_limit reached (grill
        decision 7: explicit replacement, not auto-overflow)."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            if task.warm:
                return  # idempotent
            warm_count = len(snap.warm_tasks())
            if warm_count >= self._warm_limit:
                raise WarmFull(
                    self._warm_limit,
                    tuple(t.task_id for t in snap.warm_tasks()),
                )
            now = _now_iso()
            if task.status == TaskStatus.BACKLOG:
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        task_id,
                        {"new_status": TaskStatus.READY.value},
                    )
                )
                if task.primary_work_item_id:
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.READY,
                            task_id,
                            now,
                        )
                    )
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_WARM_CHANGED, task_id, {"warm": True}
                )
            )

    def demote_to_backlog(self, task_id: str) -> None:
        """warm → backlog (warm=False, status=backlog). Foreground task cannot
        be demoted — release foreground first."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            if self._read_foreground_task_id() == task_id:
                raise TaskCommandError(
                    f"cannot demote foreground task {task_id!r}; "
                    f"release foreground first"
                )
            now = _now_iso()
            if task.warm:
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_WARM_CHANGED, task_id, {"warm": False}
                    )
                )
            if task.status != TaskStatus.BACKLOG:
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        task_id,
                        {"new_status": TaskStatus.BACKLOG.value},
                    )
                )
                if task.primary_work_item_id:
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.PENDING,
                            task_id,
                            now,
                        )
                    )

    def claim_foreground(self, task_id: str) -> None:
        """Atomically claim foreground: Task ready→running, WorkItem→RUNNING,
        foreground_claim.task_id = this task. Raises ForegroundConflict if
        another task already holds it (pass 1). Requires warm (foreground ⇒
        warm, pass 3)."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            if not task.warm:
                raise TaskCommandError(
                    f"task {task_id!r} must be warm before claiming foreground"
                )
            # Source state: READY (normal ready→running) or RUNNING (idempotent
            # re-claim below). BACKLOG/waiting/incubating cannot leap straight
            # to running (codex review HIGH 2).
            self._require_status(task, {TaskStatus.READY, TaskStatus.RUNNING})
            current = self._read_foreground_task_id()
            if current == task_id:
                return  # idempotent
            if current is not None:
                raise ForegroundConflict(current)
            now = _now_iso()
            # Take the foreground slot. IMMEDIATE has already serialised us
            # against other writers, and the ``current`` checks above ruled
            # out "already mine" (idempotent return) and "someone else holds
            # it" (ForegroundConflict) — so reaching here means the slot is
            # empty. The ``task_id IS NULL`` WHERE + rowcount check is a
            # defensive backstop in case a future caller bypasses the read.
            cur = self._conn.execute(
                "UPDATE foreground_claim SET task_id=? WHERE id=1 "
                "AND task_id IS NULL",
                (task_id,),
            )
            if cur.rowcount == 0:
                raise ForegroundConflict(self._read_foreground_task_id())
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_STATUS_CHANGED,
                    task_id,
                    {"new_status": TaskStatus.RUNNING.value},
                )
            )
            if task.primary_work_item_id:
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.RUNNING,
                        task_id,
                        now,
                    )
                )
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.FOREGROUND_CLAIMED, task_id, {"task_id": task_id}
                )
            )

    def release_foreground(self) -> None:
        """Release foreground: Task running→ready, WorkItem→READY, claim
        cleared. Idempotent if no foreground is held."""

        assert self._conn is not None
        with self._tx():
            current = self._read_foreground_task_id()
            if current is None:
                return
            snap = self.replay()
            now = _now_iso()
            self._conn.execute(
                "UPDATE foreground_claim SET task_id=NULL WHERE id=1"
            )
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.FOREGROUND_RELEASED, current, {}
                )
            )
            task = next((t for t in snap.tasks if t.task_id == current), None)
            if task is not None and not task.status.is_terminal:
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        current,
                        {"new_status": TaskStatus.READY.value},
                    )
                )
                if task.primary_work_item_id:
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.READY,
                            current,
                            now,
                        )
                    )

    # --------------------------------------------------------------- waiting

    def _set_waiting(self, task_id: str, waiting: WaitingCondition) -> None:
        """Shared body for set_waiting_user / _event / _incubating (opens tx).

        Source state must be RUNNING (the frozen graph is running→waiting_*);
        a backlog/ready Task cannot leap into waiting (codex review HIGH 2)."""

        with self._tx():
            self._set_waiting_in_tx(task_id, waiting)

    def _set_waiting_in_tx(
        self,
        task_id: str,
        waiting: WaitingCondition,
        snap: Snapshot | None = None,
    ) -> None:
        """Body of ``_set_waiting``; caller holds the transaction.

        slice-087: ``suspend_episode`` calls this inside its own composite tx
        so the Episode suspend + Task waiting + foreground CAS + WorkItem
        SUSPENDED are atomic. The TASK_WAITING_SET payload now carries
        ``subtype`` + ``episode_id`` so the reducer's WaitingCondition
        projection mirrors the owning Episode's pending (grill decision 1,
        14). Pre-087 callers leave them None (back-compat).

        ``snap`` (codex R3-L2): an already-replayed snapshot the caller holds.
        ``suspend_episode`` has just replayed; passing it avoids a second full
        replay+reduction over the journal inside this helper (a measurable
        cost under long journals, and the replay holds the IMMEDIATE lock).
        """

        assert self._conn is not None
        if snap is None:
            snap = self.replay()
        task = self._require_task(snap, task_id)
        self._require_non_terminal(task)
        self._require_status(task, {TaskStatus.RUNNING})
        now = _now_iso()
        if self._read_foreground_task_id() == task_id:
            self._release_foreground_in_tx(task_id)
        if task.primary_work_item_id:
            self._insert_event_in_tx(
                self._work_item_status_event(
                    task.primary_work_item_id,
                    WorkItemStatus.SUSPENDED,
                    task_id,
                    now,
                )
            )
        self._insert_event_in_tx(
            self._make_task_event(
                EventKind.TASK_WAITING_SET,
                task_id,
                {
                    "kind": waiting.kind,
                    "cause": waiting.cause,
                    "subtype": waiting.subtype.value if waiting.subtype else None,
                    "episode_id": waiting.episode_id,
                    "correlation_id": waiting.correlation_id,
                    "deadline": waiting.deadline,
                    "condition_kind": waiting.condition_kind,
                    "target_ref": waiting.target_ref,
                    "match_params": waiting.match_params,
                    "open_question": waiting.open_question,
                    "preparation_snapshot_ref": waiting.preparation_snapshot_ref,
                    "earliest_review_at": waiting.earliest_review_at,
                },
            )
        )

    def set_waiting_user(
        self,
        task_id: str,
        *,
        cause: str,
        correlation_id: str,
        deadline: str | None = None,
    ) -> None:
        """running → waiting_user (releases foreground). Matcher (user reply)
        is slice-095; slice-086 only stores the structure.

        ``correlation_id`` is mandatory — it links the waiting Task to the
        user reply that will resume it (codex review M3)."""

        if not cause:
            raise TaskCommandError("waiting_user cause must be non-empty")
        if not correlation_id:
            raise TaskCommandError("waiting_user requires correlation_id")
        self._set_waiting(
            task_id,
            WaitingCondition(
                kind=TaskStatus.WAITING_USER.value,
                cause=cause,
                correlation_id=correlation_id,
                deadline=deadline,
            ),
        )

    def set_waiting_event(
        self,
        task_id: str,
        *,
        cause: str,
        condition_kind: str,
        target_ref: str,
        match_params: dict[str, Any] | None = None,
        deadline: str | None = None,
    ) -> None:
        """running → waiting_event. Requires condition_kind + target_ref
        (the external predicate); matcher is slice-095."""

        if not cause:
            raise TaskCommandError("waiting_event cause must be non-empty")
        if not condition_kind or not target_ref:
            raise TaskCommandError(
                "waiting_event requires condition_kind and target_ref"
            )
        self._set_waiting(
            task_id,
            WaitingCondition(
                kind=TaskStatus.WAITING_EVENT.value,
                cause=cause,
                condition_kind=condition_kind,
                target_ref=target_ref,
                match_params=match_params,
                deadline=deadline,
            ),
        )

    def set_incubating(
        self,
        task_id: str,
        *,
        open_question: str,
        preparation_snapshot_ref: str,
        earliest_review_at: str | None = None,
    ) -> None:
        """running → incubating. Requires open_question + preparation_snapshot
        (architecture.md: "必须先有准备 snapshot 和明确未解问题"). Review/reframe
        is slice-098/099."""

        if not open_question or not preparation_snapshot_ref:
            raise TaskCommandError(
                "incubating requires open_question and preparation_snapshot_ref"
            )
        self._set_waiting(
            task_id,
            WaitingCondition(
                kind=TaskStatus.INCUBATING.value,
                cause=open_question,
                open_question=open_question,
                preparation_snapshot_ref=preparation_snapshot_ref,
                earliest_review_at=earliest_review_at,
            ),
        )

    def clear_waiting(self, task_id: str) -> None:
        """waiting_* → ready. The waiting condition is cleared; matcher
        satisfaction is slice-095/098."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            if task.status not in (
                TaskStatus.WAITING_USER,
                TaskStatus.WAITING_EVENT,
                TaskStatus.INCUBATING,
            ):
                raise TaskCommandError(
                    f"task {task_id!r} is not waiting (status={task.status.value})"
                )
            now = _now_iso()
            if task.primary_work_item_id:
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.READY,
                        task_id,
                        now,
                    )
                )
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_WAITING_CLEARED, task_id, {}
                )
            )

    # ------------------------------------------------------------- terminals

    def complete_task(
        self,
        task_id: str,
        *,
        confirmed_by: str,
        evidence_refs: tuple[str, ...] = (),
        confirmation_provenance: Provenance = Provenance.USER_DECISION,
    ) -> None:
        """Mark a Task done. Records confirmer + evidence + provenance (pass
        10). USER_REQUEST tasks require USER_DECISION — a model self-report
        cannot close a human task. Foreground is released in the same tx."""

        assert self._conn is not None
        if not confirmed_by:
            raise TaskCommandError("confirmed_by must be non-empty")
        if not evidence_refs:
            raise TaskCommandError(
                "evidence_refs must be non-empty (model self-report is not "
                "sufficient — codex review M2)"
            )
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            if (
                task.origin == TaskOrigin.USER_REQUEST
                and confirmation_provenance != Provenance.USER_DECISION
            ):
                raise TaskCommandError(
                    f"user-requested task {task_id!r} completion requires "
                    f"USER_DECISION (got {confirmation_provenance.value})"
                )
            # Source state: RUNNING (frozen graph running→done). A waiting or
            # backlog Task cannot be completed directly (codex review HIGH 2).
            self._require_status(task, {TaskStatus.RUNNING})
            if self._read_foreground_task_id() == task_id:
                self._release_foreground_in_tx(task_id)
            now = _now_iso()
            if task.primary_work_item_id:
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.DONE,
                        task_id,
                        now,
                    )
                )
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_COMPLETED,
                    task_id,
                    {
                        "confirmed_by": confirmed_by,
                        "confirmation_provenance": confirmation_provenance.value,
                        "evidence_refs": list(evidence_refs),
                    },
                    confirmation_provenance,
                )
            )

    def cancel_task(self, task_id: str, *, reason: str) -> None:
        """Cancel a Task (terminal). Foreground released in the same tx; no
        orphan claim (pass 6)."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            if self._read_foreground_task_id() == task_id:
                self._release_foreground_in_tx(task_id)
            now = _now_iso()
            if task.primary_work_item_id:
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.CANCELLED,
                        task_id,
                        now,
                    )
                )
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_CANCELLED,
                    task_id,
                    {"reason": reason},
                )
            )

    def record_task_error(
        self,
        task_id: str,
        *,
        reason: str,
        last_snapshot_ref: str | None = None,
        last_episode_ref: str | None = None,
        recovery_hint: str | None = None,
    ) -> None:
        """Record a task-level failure (terminal). WorkItem → FAILED (pass 13);
        ``last_snapshot_ref`` preserved for later reopen (pass 11). Foreground
        released in the same tx. Transient Episode/tool failures do NOT come
        here — those return the Task to ready (Temporal activity-retry)."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            if self._read_foreground_task_id() == task_id:
                self._release_foreground_in_tx(task_id)
            now = _now_iso()
            if task.primary_work_item_id:
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.FAILED,
                        task_id,
                        now,
                    )
                )
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_ERROR_RECORDED,
                    task_id,
                    {
                        "origin": task.origin.value,
                        "failure_reason": reason,
                        "last_snapshot_ref": last_snapshot_ref,
                        "last_episode_ref": last_episode_ref,
                        "recovery_hint": recovery_hint,
                    },
                )
            )

    # ----------------------------------------------------- non-state updates

    def append_constraint(self, task_id: str, constraint: str) -> None:
        """Append a user-clarified constraint. original_goal is never
        overwritten (pass: original_goal immutable)."""

        assert self._conn is not None
        if not constraint:
            raise TaskCommandError("constraint must be non-empty")
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_CONSTRAINT_APPENDED,
                    task_id,
                    {"constraint": constraint},
                )
            )

    def set_warm_rank(self, task_id: str, warm_rank: int | None) -> None:
        """Set / clear the user-controlled warm ordering (pass: warm order
        by created_at, user can reorder)."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_WARM_RANK_SET,
                    task_id,
                    {"warm_rank": warm_rank},
                )
            )

    def change_authorization(
        self,
        task_id: str,
        *,
        authorization_scope: str,
        confirmed_by: str,
    ) -> None:
        """Update a Task's authorization scope.

        Records ``confirmed_by`` so audit can distinguish user-driven from
        kernel-driven scope changes — mirrors ``complete_task``'s confirmer
        discipline (a scope change is a security-sensitive act: it controls
        which tools/resources the Task may touch). Refused on terminal tasks
        (a cancelled/done/errored Task must not be silently re-authorised).
        Authority comes from the command boundary (only the kernel calls this
        in response to a user decision channel), not from provenance — which
        is why ``confirmed_by`` is an explicit parameter rather than a
        provenance the caller could self-assign.
        """

        assert self._conn is not None
        if not authorization_scope:
            raise TaskCommandError("authorization_scope must be non-empty")
        with self._tx():
            snap = self.replay()
            task = self._require_task(snap, task_id)
            self._require_non_terminal(task)
            self._insert_event_in_tx(
                self._make_task_event(
                    EventKind.TASK_AUTHORIZATION_CHANGED,
                    task_id,
                    {
                        "authorization_scope": authorization_scope,
                        "confirmed_by": confirmed_by,
                    },
                    Provenance.USER_DECISION,
                )
            )

    # ---------------------------------------------------------- episode commands
    #
    # slice-087 structured command entry points for the Episode lifecycle.
    # Each fenced command runs inside one IMMEDIATE tx: replay state, validate,
    # check caller-held ownership (lease_id/owner/token), append a fenced
    # journal event, and (where relevant) mutate the snapshot row / lease — all
    # atomic. Fencing is enforced by KIND (_EPISODE_FENCED_KINDS), not by
    # field-presence: a stale writer cannot omit the token to bypass the check
    # (codex review of slice-087 overturned the original "optional field" plan).

    _EPISODE_OWNERSHIP_RESOURCE_TYPE = "episode_ownership"

    def _read_episode_lease_row(self, episode_id: str) -> sqlite3.Row | None:
        """Return the active ownership lease row for episode_id, or None."""

        assert self._conn is not None
        return self._conn.execute(
            "SELECT * FROM leases WHERE resource_type='episode_ownership' "
            "AND resource_id=? AND released_at IS NULL",
            (episode_id,),
        ).fetchone()

    def _check_ownership_in_tx(
        self,
        episode_id: str,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """Validate the caller really holds this Episode's ownership lease.

        All of (lease_id, owner, fencing_token) must match the live lease; it
        must be unreleased and not expired. Mismatch → ``StaleWriterRejected``;
        the fenced command catches it and records ``late_write_rejected``.

        Expiry uses wall-clock now: an expired-but-not-taken-over lease is also
        rejected — authority ends the moment it expires, before any takeover.
        """

        assert self._conn is not None
        row = self._read_episode_lease_row(episode_id)
        now_str = _now_iso()
        if row is None:
            raise StaleWriterRejected(
                episode_id, "no active ownership lease", expected_token, None
            )
        current_token = int(row["fencing_token"])
        if row["lease_id"] != expected_lease_id:
            raise StaleWriterRejected(
                episode_id, "lease_id mismatch", expected_token, current_token
            )
        if row["owner"] != expected_owner:
            raise StaleWriterRejected(
                episode_id, "owner mismatch", expected_token, current_token
            )
        if current_token != expected_token:
            raise StaleWriterRejected(
                episode_id,
                "fencing_token mismatch (stale writer)",
                expected_token,
                current_token,
            )
        if row["expires_at"] <= now_str:
            raise StaleWriterRejected(
                episode_id, "lease expired", expected_token, current_token
            )

    def _reject_stale_write_in_tx(
        self, event: EventEnvelope, exc: StaleWriterRejected
    ) -> None:
        """Record a ``late_write_rejected`` audit event for a rejected stale
        write. Attempted kind / token / reason stored; no authoritative state
        change. Caller holds the transaction."""

        assert self._conn is not None
        audit = EventEnvelope(
            event_id=f"late_write.{uuid4().hex}",
            kind=EventKind.LATE_WRITE_REJECTED,
            occurred_at=_now_iso(),
            source="kernel",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload={
                "episode_id": exc.episode_id,
                "attempted_kind": event.kind,
                "attempted_event_id": event.event_id,
                "attempted_token": exc.attempted_token,
                "current_token": exc.current_token,
                "reason": exc.reason,
            },
            episode_id=exc.episode_id,
        )
        payload_text, payload_hash = _payload_json(audit.payload)
        self._conn.execute(
            _EVENT_INSERT_SQL, _event_params(audit, payload_text, payload_hash)
        )

    def _append_fenced_event_in_tx(self, event: EventEnvelope) -> int:
        """Append a fenced Episode event, validating ownership first.

        Idempotent on ``event_id``: if the same event is already persisted
        (same id AND payload hash) the original seq is returned WITHOUT
        consulting fencing — a read-only retry by a possibly-stale caller must
        not error. Same id / different content → ``ValueError``. Otherwise the
        ownership lease is checked before the write; on stale-writer rejection
        a ``late_write_rejected`` audit event is recorded and the exception
        re-raised.
        """

        assert self._conn is not None
        if event.kind not in _EPISODE_FENCED_KINDS:
            raise EpisodeCommandError(
                f"_append_fenced_event_in_tx called with non-fenced kind "
                f"{event.kind!r}"
            )
        if (
            event.episode_id is None
            or event.lease_id is None
            or event.owner is None
            or event.fencing_token is None
        ):
            raise EpisodeCommandError(
                f"fenced event {event.kind!r} must carry episode_id, lease_id, "
                f"owner, fencing_token"
            )
        payload_text, payload_hash = _payload_json(event.payload)
        existing = self._conn.execute(
            "SELECT * FROM events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            # codex H7: idempotent only if the FULL identity matches (kind /
            # entity / lease triple / payload_hash), not payload_hash alone.
            if _event_row_identity(existing, payload_hash) != _event_identity(
                event, payload_hash
            ):
                raise ValueError(
                    f"event_id {event.event_id!r} already exists with different "
                    f"content (identity mismatch)"
                )
            return int(existing["seq"])
        try:
            self._check_ownership_in_tx(
                event.episode_id, event.lease_id, event.owner, event.fencing_token
            )
        except StaleWriterRejected as exc:
            # codex M1: do NOT write the audit inline — this tx is about to
            # roll back (the caller's ``_tx`` re-raises), which would undo the
            # audit row. Attach the attempted event so the outermost ``_tx``
            # can record it in a FRESH transaction after rollback.
            exc.attempted_event = event  # type: ignore[attr-defined]
            raise
        self._conn.execute(
            _EVENT_INSERT_SQL, _event_params(event, payload_text, payload_hash)
        )
        row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    def _make_episode_event(
        self,
        kind: str,
        episode_id: str,
        payload: dict[str, Any],
        *,
        work_item_id: str | None = None,
        task_id: str | None = None,
        provenance: Provenance = Provenance.MACHINE_OBSERVATION,
        lease_id: str | None = None,
        owner: str | None = None,
        fencing_token: int | None = None,
        event_id: str | None = None,
    ) -> EventEnvelope:
        """Build an Episode event. Fenced kinds pass lease_id/owner/token.

        ``event_id`` lets a caller that pre-generated a stable id (e.g. the
        snapshot row's ``committed_event_id``) use it as the event's primary
        key, so the snapshot↔event link is real and a read can verify it
        (codex M2 / spec line 222)."""

        return EventEnvelope(
            event_id=event_id or f"{kind}.{uuid4().hex}",
            kind=kind,
            occurred_at=_now_iso(),
            source="kernel",
            provenance=provenance,
            policy_version=self._policy_version,
            payload=payload,
            work_item_id=work_item_id,
            task_id=task_id,
            episode_id=episode_id,
            lease_id=lease_id,
            owner=owner,
            fencing_token=fencing_token,
        )

    def _require_episode(self, snap: Snapshot, episode_id: str) -> EpisodeState:
        """Return the EpisodeState or raise EpisodeCommandError."""

        ep = snap.episode_by_id(episode_id)
        if ep is None:
            raise EpisodeCommandError(f"unknown episode_id={episode_id!r}")
        return ep

    def _episode_state_to_episode(
        self, state: EpisodeState, ownership_lease_id: str | None = None
    ) -> Episode:
        """Project a reducer EpisodeState into the public Episode value object
        (drops status_provenance). ``ownership_lease_id`` is filled from the
        live lease table by the caller (live state, not folded)."""

        return Episode(
            episode_id=state.episode_id,
            work_item_id=state.work_item_id,
            task_id=state.task_id,
            status=state.status,
            native_session_id=state.native_session_id,
            ownership_lease_id=ownership_lease_id,
            last_snapshot_ref=state.last_snapshot_ref,
            pending_descriptor=state.pending_descriptor,
            reconcile_reason=state.reconcile_reason,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    def _next_snapshot_version_in_tx(self, episode_id: str) -> int:
        """Next version number for a new snapshot of this Episode."""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM episode_snapshots "
            "WHERE episode_id=?",
            (episode_id,),
        ).fetchone()
        return int(row["v"]) + 1

    # --------------------------------------------------------------- ownership

    def acquire_episode_ownership(
        self,
        episode_id: str,
        *,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None = None,
    ) -> Lease:
        """Acquire (or reclaim) the ownership lease for an Episode.

        Thin wrapper over ``acquire_lease`` with resource_type =
        ``episode_ownership``. Returns the lease carrying the fencing token the
        caller must present on subsequent fenced writes. Idempotent on
        ``idempotency_key`` AND same owner (caller's retry identity for THIS
        episode).
        """

        return self.acquire_lease(
            resource_type=self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
            resource_id=episode_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
            idempotency_key=idempotency_key,
        )

    def release_episode_ownership(self, episode_id: str) -> bool:
        """Release the active ownership lease for an Episode.

        Returns True if a lease was active. Used by takeover / shutdown paths;
        normal Episode flow releases via close/fail/suspend which also write
        the lifecycle event atomically.
        """

        assert self._conn is not None
        row = self._read_episode_lease_row(episode_id)
        if row is None:
            return False
        return self.release_lease(row["lease_id"])

    def _grant_episode_ownership_in_tx(
        self,
        episode_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None,
    ) -> Lease:
        """Grant the ownership lease INSIDE the caller's open transaction.

        Used by ``start_episode`` so the Episode row + lease + idempotency key
        land atomically. Does not run ``acquire_lease`` (which opens its own
        tx); mirrors its grant path: idempotent re-claim, fence-counter bump,
        lease INSERT, ownership_acquired audit event.
        """

        assert self._conn is not None
        now_str = _now_iso()
        expires_str = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type='episode_ownership' "
                "AND resource_id=? AND idempotency_key=? AND released_at IS NULL",
                (episode_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise LeaseConflict("episode_ownership", episode_id)
                # codex R3-M3: do not hand back an already-expired lease — the
                # caller would fail the first fenced write. A retry that lands
                # after TTL is a conflict (the caller must re-acquire fresh).
                if existing["expires_at"] <= now_str:
                    raise LeaseConflict("episode_ownership", episode_id)
                return _lease_from_row(existing)
        token = self._next_fence_token_in_tx(
            self._EPISODE_OWNERSHIP_RESOURCE_TYPE, episode_id
        )
        lease_id = uuid4().hex
        try:
            self._conn.execute(
                "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                "acquired_at, expires_at, idempotency_key, released_at, "
                "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    lease_id,
                    self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
                    episode_id,
                    owner,
                    now_str,
                    expires_str,
                    idempotency_key,
                    token,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # codex R3-H3: surface a collision as LeaseConflict, never a raw
            # sqlite3 error (idempotency_key still in use, or another writer
            # raced the grant/takeover).
            raise LeaseConflict("episode_ownership", episode_id) from exc
        self._insert_event_in_tx(
            EventEnvelope(
                event_id=f"episode.ownership_acquired.{uuid4().hex}",
                kind=EventKind.EPISODE_OWNERSHIP_ACQUIRED,
                occurred_at=now_str,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=self._policy_version,
                payload={
                    "lease_id": lease_id,
                    "owner": owner,
                    "fencing_token": token,
                    "expires_at": expires_str,
                },
                episode_id=episode_id,
            )
        )
        return Lease(
            lease_id=lease_id,
            resource_type=self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
            resource_id=episode_id,
            owner=owner,
            acquired_at=now_str,
            expires_at=expires_str,
            idempotency_key=idempotency_key,
            fencing_token=token,
        )

    # --------------------------------------------------------------- lifecycle

    def start_episode(
        self,
        *,
        work_item_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str,
        task_id: str | None = None,
        previous_snapshot_ref: SnapshotRef | None = None,
    ) -> tuple[Episode, Lease]:
        """Create an Episode in STARTING + acquire its ownership lease, atomically.

        ``previous_snapshot_ref`` is the接力 base (None for a first Episode);
        recorded for 090 / recovery but no fenced check is made yet — the
        Episode is STARTING, no fenced progress write happens until 090 moves
        it to ACTIVE. ``native_session_id`` is left None (090 binds it).

        STARTING has no public →ACTIVE command in 087 (spec line 56: 090 binds
        the native session and flips STARTING → ACTIVE). 087 tests use the
        internal ``_append_fenced_event_in_tx`` helper to reach ACTIVE; 090
        MUST add the public command.

        Idempotent on ``idempotency_key``: a retry returns the original
        (Episode, Lease) ONLY IF its ownership lease is still active AND still
        owned by the same caller. If the lease expired and a new runner took
        over (recover_episode), the retry raises ``LeaseConflict`` rather than
        handing back someone else's lease (codex R3-H4 / R3-M6). If the lease
        was released (Episode closed/failed), the retry raises
        ``EpisodeCommandError``.
        """

        assert self._conn is not None
        if not work_item_id:
            raise EpisodeCommandError("work_item_id must be non-empty")
        if not owner or ttl_seconds <= 0:
            raise EpisodeCommandError("owner and positive ttl required")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise EpisodeCommandError("idempotency_key must be a non-empty string")
        with self._tx():
            existing = self._conn.execute(
                "SELECT episode_id FROM episode_create_keys WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                episode_id = existing["episode_id"]
                lease_row = self._read_episode_lease_row(episode_id)
                snap = self.replay()
                lease = _lease_from_row(lease_row) if lease_row is not None else None
                if lease is None:
                    raise EpisodeCommandError(
                        f"idempotent replay: episode {episode_id!r} has no active "
                        f"ownership lease (lease expired before retry)"
                    )
                # codex R3-H4: the active lease must still belong to THIS owner.
                # If the original owner's lease expired and a new runner took
                # over (recover_episode), the active lease is now the new
                # owner's — silently returning it would hand the retrier someone
                # else's lease. Mirror _recover_ownership_in_tx's owner check.
                if lease.owner != owner:
                    raise LeaseConflict(
                        "episode_ownership", episode_id
                    )
                # codex R3-L5 / R3-M3: do not return an already-expired lease —
                # the public Episode.ownership_lease_id must not point at a
                # dead lease (and the caller's first fenced write would fail).
                if lease.expires_at <= _now_iso():
                    raise LeaseConflict("episode_ownership", episode_id)
                return (
                    self._episode_state_to_episode(
                        self._require_episode(snap, episode_id), lease.lease_id
                    ),
                    lease,
                )

            episode_id = uuid4().hex
            now = _now_iso()
            # codex H2: verify the binding BEFORE creating the Episode, so a
            # dangling work_item_id / task_id cannot produce an Episode whose
            # suspend path would later silently skip the missing half. The
            # WorkItem must exist; if a task_id is given it must match the
            # WorkItem's task_id (a Task Episode binds to that Task's primary
            # WorkItem).
            snap = self.replay()
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == work_item_id),
                None,
            )
            if work_item is None:
                raise EpisodeCommandError(
                    f"work_item_id {work_item_id!r} does not exist; cannot "
                    f"bind an Episode to a missing WorkItem"
                )
            if task_id is not None:
                if work_item.task_id != task_id:
                    raise EpisodeCommandError(
                        f"task_id {task_id!r} does not match WorkItem "
                        f"{work_item_id!r} (work_item.task_id="
                        f"{work_item.task_id!r})"
                    )
                if not any(t.task_id == task_id for t in snap.tasks):
                    raise EpisodeCommandError(
                        f"task_id {task_id!r} does not exist"
                    )
            elif work_item.task_id is not None:
                # codex N8 (round 2): a WorkItem that already carries a task_id
                # (TASK OR INCUBATION) must be bound to that Task explicitly.
                # The previous check only caught kind==TASK, letting an
                # INCUBATION WorkItem (which also has a task_id) be started as a
                # task-less system Episode — its suspend/activate would then
                # wrongly take the no-Task branch.
                raise EpisodeCommandError(
                    f"WorkItem {work_item_id!r} (kind={work_item.kind.value}) "
                    f"is bound to task {work_item.task_id!r}; pass that "
                    f"task_id to bind a Task Episode"
                )
            self._insert_event_in_tx(
                EventEnvelope(
                    event_id=f"episode.create.{episode_id}",
                    kind=EventKind.EPISODE_CREATED,
                    occurred_at=now,
                    source="kernel",
                    provenance=Provenance.MACHINE_OBSERVATION,
                    policy_version=self._policy_version,
                    payload={
                        "episode_id": episode_id,
                        "work_item_id": work_item_id,
                        "task_id": task_id,
                        "status": EpisodeStatus.STARTING.value,
                        "native_session_id": None,
                        "previous_snapshot_ref": (
                            {
                                "episode_id": previous_snapshot_ref.episode_id,
                                "version": previous_snapshot_ref.version,
                                "committed_event_id": previous_snapshot_ref.committed_event_id,
                                "payload_hash": previous_snapshot_ref.payload_hash,
                            }
                            if previous_snapshot_ref
                            else None
                        ),
                    },
                    work_item_id=work_item_id,
                    task_id=task_id,
                    episode_id=episode_id,
                )
            )
            lease = self._grant_episode_ownership_in_tx(
                episode_id, owner, ttl_seconds, idempotency_key
            )
            self._conn.execute(
                "INSERT INTO episode_create_keys (idempotency_key, episode_id, "
                "created_at) VALUES (?, ?, ?)",
                (idempotency_key, episode_id, now),
            )
        snap = self.replay()
        return (
            self._episode_state_to_episode(
                self._require_episode(snap, episode_id), lease.lease_id
            ),
            lease,
        )

    def _fenced_status_change_in_tx(
        self,
        *,
        episode_id: str,
        kind: str,
        new_status: EpisodeStatus,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        extra_payload: dict[str, Any] | None = None,
        work_item_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Shared body for fenced pure-status transitions (yield / close / fail
        / activate / recovering). Caller holds the tx."""

        payload: dict[str, Any] = {"new_status": new_status.value}
        if extra_payload:
            payload.update(extra_payload)
        self._append_fenced_event_in_tx(
            self._make_episode_event(
                kind,
                episode_id,
                payload,
                work_item_id=work_item_id,
                task_id=task_id,
                lease_id=expected_lease_id,
                owner=expected_owner,
                fencing_token=expected_token,
            )
        )

    def request_yield(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        reason: str,
    ) -> None:
        """active → yield_requested (fenced). Allows in-flight tools to finish;
        089 then drives checkpoint → close."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.ACTIVE:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be ACTIVE to request yield "
                    f"(got {ep.status.value})"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_YIELD_REQUESTED,
                new_status=EpisodeStatus.YIELD_REQUESTED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"reason": reason},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )

    def commit_checkpoint(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        snapshot: EpisodeSnapshot,
        checkpoint_key: str,
    ) -> SnapshotRef:
        """Commit a cooperative snapshot for the Episode (fenced), atomically.

        snapshot row (version N+1) + fenced ``episode.checkpoint_committed``
        event land in one tx. The Episode always moves to ``checkpointing``
        (the command fixes the target — a caller cannot pick another state).
        Idempotent on ``checkpoint_key``: a crash retry with the same key
        returns the original SnapshotRef without minting a second version.
        ``snapshot.source`` must be COOPERATIVE; ``recovery_partial`` goes
        through ``checkpoint_recovery_partial``.

        Source states: ``active`` (a cooperative checkpoint mid-work) or
        ``yield_requested`` (the normal wind-down after request_yield). Blocked
        states (suspended / reconcile_required / recovering) cannot checkpoint
        here — they have their own exit commands.
        """

        assert self._conn is not None
        if not isinstance(checkpoint_key, str) or not checkpoint_key.strip():
            raise EpisodeCommandError("checkpoint_key must be non-empty")
        if snapshot.source != SnapshotSource.COOPERATIVE:
            raise EpisodeCommandError(
                "commit_checkpoint is for cooperative snapshots; use "
                "checkpoint_recovery_partial for recovery_partial"
            )
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            # codex N2 (round 2): resolve an idempotent retry BEFORE the status
            # gate. A crash after COMMIT but before the response leaves the
            # Episode in CHECKPOINTING; the retry would then fail the
            # ACTIVE/YIELD_REQUESTED check below, breaking the checkpoint_key
            # idempotency contract (pass 6). Same-episode key hit → return the
            # original ref; cross-episode hit → M3 conflict; both regardless of
            # the current status.
            existing = self._conn.execute(
                "SELECT episode_id, version, payload_hash, committed_event_id "
                "FROM episode_snapshots WHERE checkpoint_key=?",
                (checkpoint_key,),
            ).fetchone()
            if existing is not None:
                # codex M3: checkpoint_key is globally UNIQUE. A hit belonging
                # to a DIFFERENT Episode is a key-scope conflict (the caller
                # reused a key across episodes) — NOT an idempotent retry.
                if existing["episode_id"] != episode_id:
                    raise EpisodeCommandError(
                        f"checkpoint_key {checkpoint_key!r} already used by "
                        f"episode {existing['episode_id']!r}; checkpoint_key "
                        f"is globally unique — use a different key"
                    )
                return SnapshotRef(
                    episode_id=episode_id,
                    version=int(existing["version"]),
                    committed_event_id=existing["committed_event_id"],
                    payload_hash=existing["payload_hash"],
                )
            if ep.status not in (
                EpisodeStatus.ACTIVE,
                EpisodeStatus.YIELD_REQUESTED,
            ):
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be ACTIVE or YIELD_REQUESTED "
                    f"to commit a cooperative checkpoint (got {ep.status.value})"
                )
            # codex R3-M2: journal_through_seq is the high-watermark this
            # snapshot covers; it must not look past the live journal end. A
            # too-high value would silently make _collect_recovery_events skip
            # events in (real_last_seq, claimed] on the next recovery. Reject
            # rather than clamp, so a caller bug surfaces.
            if snapshot.journal_through_seq > snap.last_seq:
                raise EpisodeCommandError(
                    f"snapshot.journal_through_seq ({snapshot.journal_through_seq}) "
                    f"exceeds the live journal end ({snap.last_seq}); a checkpoint "
                    f"cannot cover events that have not happened yet"
                )
            version = self._next_snapshot_version_in_tx(episode_id)
            payload_text, payload_hash = _payload_json(
                _snapshot_to_payload(snapshot)
            )
            _validate_episode_snapshot(snapshot, payload_text)
            committed_event_id = (
                f"episode.checkpoint.{episode_id}.{version}.{uuid4().hex}"
            )
            self._conn.execute(
                "INSERT INTO episode_snapshots (episode_id, version, "
                "checkpoint_key, source, payload_json, payload_hash, "
                "base_episode_id, base_version, journal_through_seq, "
                "committed_event_id, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    version,
                    checkpoint_key,
                    snapshot.source.value,
                    payload_text,
                    payload_hash,
                    snapshot.base_snapshot_ref.episode_id
                    if snapshot.base_snapshot_ref
                    else None,
                    snapshot.base_snapshot_ref.version
                    if snapshot.base_snapshot_ref
                    else None,
                    snapshot.journal_through_seq,
                    committed_event_id,
                    _now_iso(),
                ),
            )
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_CHECKPOINT_COMMITTED,
                    episode_id,
                    {
                        "version": version,
                        "source": snapshot.source.value,
                        "payload_hash": payload_hash,
                        "journal_through_seq": snapshot.journal_through_seq,
                        "committed_event_id": committed_event_id,
                        "new_status": EpisodeStatus.CHECKPOINTING.value,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                    event_id=committed_event_id,
                )
            )
            return SnapshotRef(
                episode_id=episode_id,
                version=version,
                committed_event_id=committed_event_id,
                payload_hash=payload_hash,
            )

    def read_episode_snapshot(self, ref: SnapshotRef) -> EpisodeSnapshot:
        """Read a snapshot payload by precise ref. Fail-closed if the row is
        missing, its hash does not match the ref, or its committed event is
        absent from the journal (corruption / forgery, codex M2)."""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT payload_json, payload_hash, committed_event_id "
            "FROM episode_snapshots "
            "WHERE episode_id=? AND version=?",
            (ref.episode_id, ref.version),
        ).fetchone()
        if row is None:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} not found "
                f"(reducer referenced a row that is absent)"
            )
        if row["payload_hash"] != ref.payload_hash:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} payload_hash mismatch "
                f"(row={row['payload_hash']}, ref={ref.payload_hash})"
            )
        # codex N7 (round 2): the ref, the row and the journal event must agree
        # PRECISELY. The ref's committed_event_id (folded by the reducer) must
        # equal the row's (stored at write time), and that event must exist and
        # point back at this episode. A mere "event_id exists somewhere in the
        # journal" check let a forged ref aimed at an unrelated NOTE pass.
        committed_event_id = row["committed_event_id"]
        if ref.committed_event_id != committed_event_id:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event_id "
                f"mismatch (ref={ref.committed_event_id!r}, "
                f"row={committed_event_id!r})"
            )
        ev_row = self._conn.execute(
            "SELECT kind, episode_id FROM events WHERE event_id=?",
            (committed_event_id,),
        ).fetchone()
        if ev_row is None:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event "
                f"{committed_event_id!r} not in journal (corruption)"
            )
        if ev_row["episode_id"] != ref.episode_id:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event "
                f"{committed_event_id!r} belongs to a different episode "
                f"({ev_row['episode_id']!r})"
            )
        if ev_row["kind"] not in (
            EventKind.EPISODE_CHECKPOINT_COMMITTED,
            EventKind.EPISODE_RECONCILE_RESOLVED,
        ):
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event "
                f"{committed_event_id!r} has wrong kind {ev_row['kind']!r} "
                f"(expected checkpoint_committed or reconcile_resolved)"
            )
        return _snapshot_from_payload(json.loads(row["payload_json"]))

    def close_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """→ closed (fenced, terminal). Only legal from ``checkpointing``.

        The normal wind-down is ``request_yield → commit_checkpoint (→
        checkpointing) → close_episode (→ closed)``. Closing from any other
        state is refused — those states have their own exit path (suspend →
        resolve/activate, reconcile_required → resolve_reconcile)."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.CHECKPOINTING:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be CHECKPOINTING to close "
                    f"(got {ep.status.value}); closing from other states has "
                    f"its own exit command"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_CLOSED,
                new_status=EpisodeStatus.CLOSED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )
            # release the ownership lease in the same tx (no orphan lease)
            row = self._read_episode_lease_row(episode_id)
            if row is not None and row["lease_id"] == expected_lease_id:
                self._conn.execute(
                    "UPDATE leases SET released_at=? WHERE lease_id=?",
                    (_now_iso(), expected_lease_id),
                )

    def fail_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        reason: str,
    ) -> None:
        """→ failed (fenced, terminal). Exec-layer failure; the owning Task
        returns to READY (087 does not flip Task to error — that is Task-level
        and decided elsewhere).

        codex H4: the composite restores the Task / WorkItem to a retryable
        state and releases the foreground + ownership lease in the same tx.
        The previous code flipped only the Episode, leaving a Task RUNNING with
        the foreground held — an unrecoverable contradiction."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} already terminal ({ep.status.value})"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_FAILED,
                new_status=EpisodeStatus.FAILED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"reason": reason},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )
            now = _now_iso()
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            if ep.task_id is not None:
                task = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                if task is not None and not task.status.is_terminal:
                    # release foreground if this Task holds it (CAS), then
                    # return the Task to READY (retryable, not error).
                    if self._read_foreground_task_id() == task.task_id:
                        self._release_foreground_in_tx(task.task_id)
                    if task.status != TaskStatus.READY:
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_STATUS_CHANGED,
                                task.task_id,
                                {"new_status": TaskStatus.READY.value},
                            )
                        )
            if (
                work_item is not None
                and work_item.status
                in (WorkItemStatus.RUNNING, WorkItemStatus.SUSPENDED)
            ):
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.READY,
                        ep.task_id,
                        now,
                    )
                )
            row = self._read_episode_lease_row(episode_id)
            if row is not None and row["lease_id"] == expected_lease_id:
                self._conn.execute(
                    "UPDATE leases SET released_at=? WHERE lease_id=?",
                    (_now_iso(), expected_lease_id),
                )

    # --------------------------------------------------- suspend / resume (2-phase)

    def suspend_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        pending: PendingDescriptor,
    ) -> None:
        """active → suspended_waiting_* (fenced, composite).

        Atomic same-tx: fenced EPISODE_SUSPENDED + (Task-bound) Task
        running→waiting_user with subtype/episode_id + foreground CAS release
        + WorkItem SUSPENDED. system WorkItem (no Task) skips the Task half;
        WorkLease release is 093's job (it subscribes to episode.suspended).
        """

        assert self._conn is not None
        if pending.kind not in (WaitingSubtype.INPUT, WaitingSubtype.APPROVAL):
            raise EpisodeCommandError(
                f"suspend_episode pending.kind must be INPUT or APPROVAL "
                f"(got {pending.kind.value})"
            )
        if not pending.correlation_id or not pending.cause:
            raise EpisodeCommandError("pending requires correlation_id + cause")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.ACTIVE:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be ACTIVE to suspend "
                    f"(got {ep.status.value})"
                )
            # codex H2: validate the WorkItem (+ Task, when bound) BEFORE any
            # write. The previous code silently skipped a missing or wrong-state
            # WorkItem/Task, so an Episode could enter suspended_waiting_* while
            # its Task stayed RUNNING and kept the foreground — breaking
            # foreground⇔running. Any mismatch now rejects the whole composite.
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            if work_item is None:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} bound WorkItem {ep.work_item_id!r} "
                    f"not found; cannot suspend"
                )
            if work_item.status != WorkItemStatus.RUNNING:
                raise EpisodeCommandError(
                    f"WorkItem {ep.work_item_id!r} must be RUNNING to suspend "
                    f"(got {work_item.status.value})"
                )
            task = None
            if ep.task_id is not None:
                task = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} bound task {ep.task_id!r} "
                        f"not found; cannot suspend"
                    )
                if task.status != TaskStatus.RUNNING:
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be RUNNING to suspend "
                        f"(got {task.status.value})"
                    )
            new_status = (
                EpisodeStatus.SUSPENDED_WAITING_INPUT
                if pending.kind == WaitingSubtype.INPUT
                else EpisodeStatus.SUSPENDED_WAITING_APPROVAL
            )
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_SUSPENDED,
                    episode_id,
                    {
                        "new_status": new_status.value,
                        "kind": pending.kind.value,
                        "native_generation": pending.native_generation,
                        "correlation_id": pending.correlation_id,
                        "cause": pending.cause,
                        "posed_at": pending.posed_at,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                )
            )
            # codex L1: exactly ONE owner of the WorkItem→SUSPENDED transition.
            # Task-bound: ``_set_waiting_in_tx`` emits it for the primary
            # WorkItem (== ep.work_item_id for a Task Episode) AND releases
            # foreground AND moves the Task to waiting_user. system WorkItem
            # (no Task): emit SUSPENDED here, and there is no foreground/Task
            # to touch (foreground is task-scoped).
            if task is not None:
                self._set_waiting_in_tx(
                    task.task_id,
                    WaitingCondition(
                        kind=TaskStatus.WAITING_USER.value,
                        cause=pending.cause,
                        subtype=pending.kind,
                        episode_id=episode_id,
                        correlation_id=pending.correlation_id,
                    ),
                    snap=snap,
                )
            else:
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.SUSPENDED,
                        None,
                        _now_iso(),
                    )
                )

    def resolve_episode_wait(
        self,
        episode_id: str,
        *,
        answer_correlation_id: str,
    ) -> None:
        """suspended_waiting_* → suspended_ready (NOT fenced).

        The answer has arrived but foreground is NOT claimed here: another Task
        may hold it. ``activate_suspended_episode`` later moves the Episode to
        ACTIVE once foreground is won. Not fenced because this is driven by an
        external answer arriving (095 matcher), not by the lease holder's
        progress write; the Episode is already suspended (no in-flight work).
        """

        assert self._conn is not None
        if not answer_correlation_id:
            raise EpisodeCommandError("answer_correlation_id required")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status not in (
                EpisodeStatus.SUSPENDED_WAITING_INPUT,
                EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
            ):
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be suspended_waiting_* to "
                    f"resolve wait (got {ep.status.value})"
                )
            # codex H1: the answer's correlation_id must match the pending
            # descriptor — an unrelated or late answer must not clear the real
            # pending.
            if (
                ep.pending_descriptor is None
                or ep.pending_descriptor.correlation_id != answer_correlation_id
            ):
                raise EpisodeCommandError(
                    f"answer_correlation_id {answer_correlation_id!r} does not "
                    f"match episode {episode_id!r} pending correlation_id"
                )
            # codex N4 (round 2): validate the bound WorkItem (+ Task) BEFORE any
            # write. The previous code silently skipped a missing or wrong-state
            # WorkItem/Task, so the Episode could enter suspended_ready while
            # its Task/WorkItem stayed in an incompatible state — activate would
            # then fail forever. Any mismatch now rejects the whole composite.
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            if work_item is None:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} bound WorkItem {ep.work_item_id!r} "
                    f"not found; cannot resolve wait"
                )
            if work_item.status != WorkItemStatus.SUSPENDED:
                raise EpisodeCommandError(
                    f"WorkItem {ep.work_item_id!r} must be SUSPENDED to resolve "
                    f"a wait (got {work_item.status.value})"
                )
            task = None
            if ep.task_id is not None:
                task = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} bound task {ep.task_id!r} "
                        f"not found; cannot resolve wait"
                    )
                if task.status != TaskStatus.WAITING_USER:
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be WAITING_USER to resolve "
                        f"a wait (got {task.status.value})"
                    )
            self._insert_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_WAIT_RESOLVED,
                    episode_id,
                    {"answer_correlation_id": answer_correlation_id},
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                )
            )
            # codex H1: bring the Task (waiting_user → ready) and the WorkItem
            # (SUSPENDED → READY) back to a schedulable state, so the later
            # activate can run. Foreground is NOT claimed here (pass 10) — that
            # is activate's job.
            now = _now_iso()
            if task is not None:
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        task.task_id,
                        {"new_status": TaskStatus.READY.value},
                    )
                )
            self._insert_event_in_tx(
                self._work_item_status_event(
                    work_item.work_item_id,
                    WorkItemStatus.READY,
                    ep.task_id,
                    now,
                )
            )

    def activate_suspended_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """suspended_ready → active (fenced). Composite: claim foreground +
        Task ready→running + WorkItem READY→RUNNING + fenced EPISODE_ACTIVATED.

        The caller (scheduler / 091) must have decided this Episode wins
        foreground. Raises ForegroundConflict if another Task holds it.
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.SUSPENDED_READY:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be SUSPENDED_READY to "
                    f"activate (got {ep.status.value})"
                )
            if ep.task_id is not None:
                current_fg = self._read_foreground_task_id()
                if current_fg is not None and current_fg != ep.task_id:
                    raise ForegroundConflict(current_fg)
                task = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} references unknown task "
                        f"{ep.task_id!r}"
                    )
                if task.status != TaskStatus.READY:
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be READY to reactivate "
                        f"(got {task.status.value})"
                    )
                cur = self._conn.execute(
                    "UPDATE foreground_claim SET task_id=? WHERE id=1 "
                    "AND task_id IS NULL",
                    (ep.task_id,),
                )
                if cur.rowcount != 1 and self._read_foreground_task_id() != ep.task_id:
                    raise ForegroundConflict(self._read_foreground_task_id())
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        ep.task_id,
                        {"new_status": TaskStatus.RUNNING.value},
                    )
                )
                if task.primary_work_item_id:
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.RUNNING,
                            ep.task_id,
                            _now_iso(),
                        )
                    )
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.FOREGROUND_CLAIMED,
                        ep.task_id,
                        {"task_id": ep.task_id},
                    )
                )
            else:
                # codex M5: system WorkItem (no Task) — restore the WorkItem
                # READY → RUNNING so the Episode is not left ACTIVE with a
                # SUSPENDED/READY WorkItem. There is no foreground to claim
                # (foreground is task-scoped).
                work_item = next(
                    (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                    None,
                )
                if work_item is not None and work_item.status in (
                    WorkItemStatus.READY,
                    WorkItemStatus.SUSPENDED,
                ):
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            work_item.work_item_id,
                            WorkItemStatus.RUNNING,
                            None,
                            _now_iso(),
                        )
                    )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_ACTIVATED,
                new_status=EpisodeStatus.ACTIVE,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )

    # --------------------------------------------------------------- reconcile

    def mark_pending_channel_lost(
        self,
        episode_id: str,
        *,
        reason: ReconcileReason,
    ) -> None:
        """suspended_* | suspended_ready → reconcile_required (system-detected).

        NOT fenced: this is kernel detection (host generation closed / startup
        scan, slice-083), called when the ownership lease may already be gone.
        The caller is the kernel, not a lease holder. ``reconcile_required``
        is a non-terminal blocked state exited only by ``resolve_reconcile``.
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status not in (
                EpisodeStatus.SUSPENDED_WAITING_INPUT,
                EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
                EpisodeStatus.SUSPENDED_READY,
            ):
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be suspended to mark channel "
                    f"lost (got {ep.status.value})"
                )
            self._insert_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_RECONCILE_REQUIRED,
                    episode_id,
                    {
                        "reason": reason.value,
                        "new_status": EpisodeStatus.RECONCILE_REQUIRED.value,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                )
            )
            # codex H3.1 + N5 + R3-H2: mirror the loss into the bound Task so
            # the 095 matcher + scheduler can tell this wait is no longer
            # auto-resumable, AND keep the Task non-schedulable while the Episode
            # is blocked. R3-H2: do NOT silently skip when the Task/WorkItem
            # state is not what we expected (a TOCTOU — the scheduler may have
            # claimed the Task between resolve and mark_lost, leaving it
            # RUNNING). Force any non-terminal Task into the reconcile-wait
            # (releasing foreground if it was claimed) and any non-terminal
            # WorkItem to SUSPENDED.
            subtype = (
                WaitingSubtype.REQUIRES_USER_RESTART
                if reason == ReconcileReason.REQUIRES_USER_RESTART
                else WaitingSubtype.RECONCILE
            )
            if ep.task_id is not None:
                task = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                if task is not None and not task.status.is_terminal:
                    # TOCTOU: scheduler claimed the Task (RUNNING + foreground)
                    # between resolve and mark_lost. Release the foreground so
                    # the Task can be pulled back to a non-schedulable wait.
                    if (
                        task.status == TaskStatus.RUNNING
                        and self._read_foreground_task_id() == task.task_id
                    ):
                        self._release_foreground_in_tx(task.task_id)
                    wc = task.waiting_condition
                    if (
                        task.status == TaskStatus.WAITING_USER
                        and wc is not None
                    ):
                        # suspended_waiting_*: preserve the original
                        # correlation/cause, just flip the subtype.
                        waiting_payload = {
                            "kind": TaskStatus.WAITING_USER.value,
                            "cause": wc.cause,
                            "subtype": subtype.value,
                            "episode_id": episode_id,
                            "correlation_id": wc.correlation_id,
                            "deadline": wc.deadline,
                            "condition_kind": wc.condition_kind,
                            "target_ref": wc.target_ref,
                            "match_params": wc.match_params,
                            "open_question": wc.open_question,
                            "preparation_snapshot_ref": wc.preparation_snapshot_ref,
                            "earliest_review_at": wc.earliest_review_at,
                        }
                    else:
                        # suspended_ready (resolve cleared the waiting) or a
                        # RUNNING Task that lost its channel: synthetic cause,
                        # correlation_id gone.
                        waiting_payload = {
                            "kind": TaskStatus.WAITING_USER.value,
                            "cause": (
                                f"pending channel lost ({reason.value}); "
                                f"reconcile required"
                            ),
                            "subtype": subtype.value,
                            "episode_id": episode_id,
                            "correlation_id": None,
                            "deadline": None,
                            "condition_kind": None,
                            "target_ref": None,
                            "match_params": None,
                            "open_question": None,
                            "preparation_snapshot_ref": None,
                            "earliest_review_at": None,
                        }
                    already_correct = (
                        task.status == TaskStatus.WAITING_USER
                        and wc is not None
                        and wc.subtype == subtype
                    )
                    if not already_correct:
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_WAITING_SET,
                                task.task_id,
                                waiting_payload,
                            )
                        )
            work_item = next(
                (
                    w
                    for w in snap.work_items
                    if w.work_item_id == ep.work_item_id
                ),
                None,
            )
            if (
                work_item is not None
                and not work_item.status.is_terminal
                and work_item.status != WorkItemStatus.SUSPENDED
            ):
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.SUSPENDED,
                        ep.task_id,
                        _now_iso(),
                    )
                )

    def resolve_reconcile(
        self,
        episode_id: str,
        *,
        decision: str,
        confirmed_by: str,
        recovery_snapshot: EpisodeSnapshot | None = None,
        recovery_checkpoint_key: str | None = None,
    ) -> None:
        """Exit reconcile_required (human/kernel decision). NOT fenced.

        - ``decision='close'``: ALWAYS leave a recovery_partial snapshot (built
          internally from the last snapshot + journal when the caller does not
          pass one) and move to CLOSED. The snapshot identity rides on THIS
          event — the close path no longer emits a separate
          ``checkpoint_committed`` (that fenced kind cannot be written unfenced
          by an external decision; codex C1 corollary).
        - ``decision='resume_safe'``: land in ``suspended_ready`` (decision 4A)
          with the Task/WorkItem restored to READY. Foreground is NOT claimed
          here — the caller runs ``activate_suspended_episode`` next.

        Provenance is ``USER_DECISION`` (codex H3.4): this is a human attestation
        that reality was checked, distinct from a kernel self-report.
        """

        assert self._conn is not None
        if decision not in ("close", "resume_safe"):
            raise EpisodeCommandError(
                f"decision must be 'close' or 'resume_safe' (got {decision!r})"
            )
        if not confirmed_by:
            raise EpisodeCommandError("confirmed_by required")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.RECONCILE_REQUIRED:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be reconcile_required "
                    f"(got {ep.status.value})"
                )
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            resolve_payload: dict[str, Any] = {
                "decision": decision,
                "confirmed_by": confirmed_by,
            }
            close_lease_row = None  # set by the close branch (R3-M5 audit)
            if decision == "close":
                snapshot_to_write, ck_key = self._reconcile_close_snapshot(
                    snap, ep, recovery_snapshot, recovery_checkpoint_key
                )
                version = self._next_snapshot_version_in_tx(episode_id)
                payload_text, payload_hash = _payload_json(
                    _snapshot_to_payload(snapshot_to_write)
                )
                _validate_episode_snapshot(snapshot_to_write, payload_text)
                committed_event_id = (
                    f"episode.reconcile_close.{episode_id}.{version}.{uuid4().hex}"
                )
                self._conn.execute(
                    "INSERT INTO episode_snapshots (episode_id, version, "
                    "checkpoint_key, source, payload_json, payload_hash, "
                    "base_episode_id, base_version, journal_through_seq, "
                    "committed_event_id, created_at) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        episode_id,
                        version,
                        ck_key,
                        snapshot_to_write.source.value,
                        payload_text,
                        payload_hash,
                        snapshot_to_write.base_snapshot_ref.episode_id
                        if snapshot_to_write.base_snapshot_ref
                        else None,
                        snapshot_to_write.base_snapshot_ref.version
                        if snapshot_to_write.base_snapshot_ref
                        else None,
                        snapshot_to_write.journal_through_seq,
                        committed_event_id,
                        _now_iso(),
                    ),
                )
                # codex R3-H1: the bound Task/WorkItem were left in a reconcile-
                # wait state by mark_pending_channel_lost (Task waiting_user with
                # a reconcile subtype, WorkItem SUSPENDED). Closing the Episode
                # without restoring them would leave them stuck — no later
                # command accepts an Episode that is already terminal. Mirror
                # fail_episode: return the Task/WorkItem to a retryable READY so
                # 090 can start fresh.
                close_now = _now_iso()
                if ep.task_id is not None:
                    close_task = next(
                        (t for t in snap.tasks if t.task_id == ep.task_id), None
                    )
                    if (
                        close_task is not None
                        and not close_task.status.is_terminal
                        and close_task.status != TaskStatus.READY
                    ):
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_STATUS_CHANGED,
                                close_task.task_id,
                                {"new_status": TaskStatus.READY.value},
                            )
                        )
                if (
                    work_item is not None
                    and work_item.status
                    in (WorkItemStatus.SUSPENDED, WorkItemStatus.RUNNING)
                ):
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            work_item.work_item_id,
                            WorkItemStatus.READY,
                            ep.task_id,
                            close_now,
                        )
                    )
                resolve_payload["version"] = version
                resolve_payload["source"] = snapshot_to_write.source.value
                resolve_payload["payload_hash"] = payload_hash
                resolve_payload["journal_through_seq"] = (
                    snapshot_to_write.journal_through_seq
                )
                resolve_payload["committed_event_id"] = committed_event_id
                # codex R3-M5: record the lease this close releases so audit can
                # attribute the release (the original owner, not just "user
                # decided to close"). resolve_reconcile has no expected_lease
                # param (it is a human decision), so this is the audit trail.
                close_lease_row = self._read_episode_lease_row(episode_id)
                if close_lease_row is not None:
                    resolve_payload["released_lease_id"] = close_lease_row[
                        "lease_id"
                    ]
                    resolve_payload["released_lease_owner"] = close_lease_row[
                        "owner"
                    ]
                else:
                    close_lease_row = None
                resolve_payload["new_status"] = EpisodeStatus.CLOSED.value
                new_status = EpisodeStatus.CLOSED
            else:  # resume_safe — decision 4A: land in suspended_ready
                now = _now_iso()
                if ep.task_id is not None:
                    task = next(
                        (t for t in snap.tasks if t.task_id == ep.task_id), None
                    )
                    if (
                        task is not None
                        and not task.status.is_terminal
                        and task.status != TaskStatus.READY
                    ):
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_STATUS_CHANGED,
                                task.task_id,
                                {"new_status": TaskStatus.READY.value},
                            )
                        )
                if (
                    work_item is not None
                    and work_item.status
                    in (WorkItemStatus.SUSPENDED, WorkItemStatus.RUNNING)
                ):
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            work_item.work_item_id,
                            WorkItemStatus.READY,
                            ep.task_id,
                            now,
                        )
                    )
                resolve_payload["new_status"] = EpisodeStatus.SUSPENDED_READY.value
                new_status = EpisodeStatus.SUSPENDED_READY
            self._insert_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_RECONCILE_RESOLVED,
                    episode_id,
                    resolve_payload,
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    provenance=Provenance.USER_DECISION,
                    # close path pre-generated committed_event_id to link the
                    # snapshot row to this event (codex M2). resume_safe has
                    # no snapshot → None → default id.
                    event_id=resolve_payload.get("committed_event_id"),
                )
            )
            if new_status == EpisodeStatus.CLOSED:
                # release the lingering ownership lease (read in the close
                # branch for the audit payload, R3-M5).
                if close_lease_row is not None:
                    self._conn.execute(
                        "UPDATE leases SET released_at=? WHERE lease_id=?",
                        (_now_iso(), close_lease_row["lease_id"]),
                    )

    def _reconcile_close_snapshot(
        self,
        snap: Snapshot,
        ep: EpisodeState,
        recovery_snapshot: EpisodeSnapshot | None,
        recovery_checkpoint_key: str | None,
    ) -> tuple[EpisodeSnapshot, str]:
        """Pick the recovery_partial snapshot for a reconcile-close.

        H3.2: if the caller supplied one, use it (must be recovery_partial);
        otherwise build one from the last snapshot + journal high-watermark so
        close never silently drops the work现场. Returns the snapshot and the
        checkpoint_key to use.
        """

        if recovery_snapshot is not None:
            if recovery_snapshot.source != SnapshotSource.RECOVERY_PARTIAL:
                raise EpisodeCommandError(
                    "resolve_reconcile close requires a recovery_partial snapshot"
                )
            ck = recovery_checkpoint_key or (
                f"reconcile-close-{ep.episode_id}-{uuid4().hex}"
            )
            return recovery_snapshot, ck

        prev_snapshot = None
        if ep.last_snapshot_ref is not None:
            prev_snapshot = self.read_episode_snapshot(ep.last_snapshot_ref)
        task_goal: str | None = None
        if ep.task_id is not None:
            tstate = next(
                (t for t in snap.tasks if t.task_id == ep.task_id), None
            )
            task_goal = tstate.original_goal if tstate is not None else None
        built = self.build_recovery_partial(
            work_item_goal=task_goal or f"work_item:{ep.work_item_id}",
            task_constraints_ref=ep.task_id,
            prev=prev_snapshot,
            prev_ref=ep.last_snapshot_ref,
            journal_through_seq=snap.last_seq,
            events=self._collect_recovery_events(
                ep.episode_id, prev_snapshot, snap.last_seq
            ),
        )
        ck = recovery_checkpoint_key or (
            f"reconcile-close-{ep.episode_id}-{uuid4().hex}"
        )
        return built, ck

    # ----------------------------------------------------------------- recovery

    @staticmethod
    def build_recovery_partial(
        *,
        work_item_goal: str,
        task_constraints_ref: str | None,
        prev: EpisodeSnapshot | None,
        journal_through_seq: int,
        prev_ref: SnapshotRef | None = None,
        events: tuple[EventEnvelope, ...] = (),
    ) -> EpisodeSnapshot:
        """Build a recovery_partial snapshot from prev + journal high-watermark.

        Pure function: no I/O, no model call. Missing slots → ``"unknown"``;
        only prev's confirmed completed (with evidence) and done side effects
        survive, PLUS done side effects recorded in the journal range
        (``events``) — so a crash between the last checkpoint and the failure
        does not lose a confirmed action (codex H6). An explicit "unverified"
        marker is added so a reader cannot mistake this for a fully-confirmed
        cooperative snapshot.

        Args:
            prev: the previous snapshot payload (None for a first recovery).
            journal_through_seq: the high-watermark the calling command fixed;
              stored on the snapshot so the next recovery does not re-fold.
            prev_ref: the SnapshotRef of ``prev`` (codex M4). The recovery's
              ``base_snapshot_ref`` points here — NOT at prev's own base or a
              fabricated dummy — so the recovery lineage stays accurate.
            events: the journal slice ``(prev.journal_through_seq, high]`` for
              this Episode. Only ``episode.side_effect_recorded`` events with
              ``outcome=done`` AND an evidence_ref are folded; unconfirmed
              actions stay unknown (never auto-replayed).
        """

        # codex N6 (round 2): preserve BOTH done and unknown side effects. A
        # done side effect is confirmed (survives into completed_with_evidence);
        # an unknown_requires_reconcile side effect MUST survive too — as
        # unknown, carrying its action_ref / idempotency_key so the resuming
        # runner knows WHICH action to check against reality before replay.
        # Dropping it (the previous behaviour) hid the action from the recovery,
        # so the runner might believe it never happened. Unknown NEVER becomes
        # done and NEVER enters completed_with_evidence.
        done_side_effects: list[SideEffectRecord] = [
            se
            for se in (prev.side_effects if prev else ())
            if se.outcome == "done"
        ]
        unknown_side_effects: list[SideEffectRecord] = [
            se
            for se in (prev.side_effects if prev else ())
            if se.outcome == "unknown_requires_reconcile"
        ]
        completed: list[tuple[str, str]] = (
            list(prev.completed_with_evidence) if prev else []
        )
        done_refs = {se.action_ref for se in done_side_effects}
        unknown_refs = {se.action_ref for se in unknown_side_effects}
        # codex H6: fold side_effect_recorded events from the journal range
        # (prev.journal_through_seq, high]. Done ones join completed; unknown
        # ones are preserved as unknown. Dedup by action_ref (done wins).
        for ev in events:
            if ev.kind != EventKind.EPISODE_SIDE_EFFECT_RECORDED:
                continue
            p = ev.payload
            action_ref = p.get("action_ref")
            if not action_ref or action_ref in done_refs:
                continue
            outcome = p.get("outcome")
            if outcome == "done":
                evidence_ref = p.get("evidence_ref")
                if not evidence_ref:
                    continue
                done_refs.add(action_ref)
                unknown_refs.discard(action_ref)
                unknown_side_effects = [
                    se for se in unknown_side_effects if se.action_ref != action_ref
                ]
                done_side_effects.append(
                    SideEffectRecord(
                        action_ref=action_ref,
                        idempotency_key=p.get("idempotency_key", ""),
                        outcome="done",
                        evidence_ref=evidence_ref,
                    )
                )
                completed.append((action_ref, evidence_ref))
            elif outcome == "unknown_requires_reconcile":
                if action_ref in unknown_refs:
                    continue
                unknown_refs.add(action_ref)
                unknown_side_effects.append(
                    SideEffectRecord(
                        action_ref=action_ref,
                        idempotency_key=p.get("idempotency_key", ""),
                        outcome="unknown_requires_reconcile",
                    )
                )
        side_effects = done_side_effects + unknown_side_effects
        if prev is not None:
            unknowns = prev.unknowns + (
                "recovery_partial: progress after base snapshot unverified",
            )
            artifacts = prev.artifacts
            transcript = prev.native_transcript_ref
            # codex M4: base points at prev's own ref, not prev's base or a
            # dummy. prev_ref may be None when the caller could not supply it
            # (kept honest rather than fabricated).
            base_ref = prev_ref
        else:
            unknowns = ("recovery_partial: no base snapshot; full state unverified",)
            artifacts = ()
            transcript = None
            base_ref = None
        return EpisodeSnapshot(
            work_item_goal=work_item_goal,
            task_constraints_ref=task_constraints_ref,
            current_judgment="unknown",
            completed_with_evidence=tuple(completed),
            side_effects=tuple(side_effects),
            unknowns=unknowns,
            waiting_condition=None,
            next_steps=(),
            artifacts=artifacts,
            native_transcript_ref=transcript,
            source=SnapshotSource.RECOVERY_PARTIAL,
            journal_through_seq=journal_through_seq,
            base_snapshot_ref=base_ref,
        )

    def _collect_recovery_events(
        self,
        episode_id: str,
        prev_snapshot: EpisodeSnapshot | None,
        high_seq: int,
    ) -> tuple[EventEnvelope, ...]:
        """Return the ``episode.side_effect_recorded`` events in the journal
        range ``(prev.journal_through_seq, high_seq]`` for this Episode.

        This is the slice ``build_recovery_partial`` folds (codex H6). Caller
        holds the transaction; ``list_events`` reads from the same connection.
        """

        from_seq = prev_snapshot.journal_through_seq if prev_snapshot else 0
        out: list[EventEnvelope] = []
        for seq, ev in self.list_events(from_seq=from_seq):
            if seq > high_seq:
                break
            if ev.episode_id != episode_id:
                continue
            if ev.kind != EventKind.EPISODE_SIDE_EFFECT_RECORDED:
                continue
            out.append(ev)
        return tuple(out)

    def checkpoint_recovery_partial(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        reason: str,
        checkpoint_key: str,
    ) -> SnapshotRef:
        """Build + commit a recovery_partial snapshot (fenced).

        Fixes the journal high-watermark, reads the previous snapshot, calls
        ``build_recovery_partial``, commits the snapshot row + fenced
        checkpoint event. Used by 089 (timeout/shutdown/hard-budget) and by
        ``recover_episode``.
        """

        assert self._conn is not None
        if not isinstance(checkpoint_key, str) or not checkpoint_key.strip():
            raise EpisodeCommandError("checkpoint_key must be non-empty")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} is terminal ({ep.status.value})"
                )
            existing = self._conn.execute(
                "SELECT episode_id, version, payload_hash, committed_event_id "
                "FROM episode_snapshots WHERE checkpoint_key=?",
                (checkpoint_key,),
            ).fetchone()
            if existing is not None:
                # codex M3: a globally-unique key hit on a DIFFERENT Episode is
                # a key-scope conflict, not an idempotent retry.
                if existing["episode_id"] != episode_id:
                    raise EpisodeCommandError(
                        f"checkpoint_key {checkpoint_key!r} already used by "
                        f"episode {existing['episode_id']!r}; use a different key"
                    )
                return SnapshotRef(
                    episode_id=episode_id,
                    version=int(existing["version"]),
                    committed_event_id=existing["committed_event_id"],
                    payload_hash=existing["payload_hash"],
                )
            prev_snapshot = None
            if ep.last_snapshot_ref is not None:
                prev_snapshot = self.read_episode_snapshot(ep.last_snapshot_ref)
            task_goal = None
            if ep.task_id is not None:
                task_state = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                task_goal = task_state.original_goal if task_state is not None else None
            work_item_goal = task_goal or f"work_item:{ep.work_item_id}"
            recovery = self.build_recovery_partial(
                work_item_goal=work_item_goal,
                task_constraints_ref=ep.task_id,
                prev=prev_snapshot,
                prev_ref=ep.last_snapshot_ref,
                journal_through_seq=snap.last_seq,
                events=self._collect_recovery_events(
                    episode_id, prev_snapshot, snap.last_seq
                ),
            )
            version = self._next_snapshot_version_in_tx(episode_id)
            payload_text, payload_hash = _payload_json(
                _snapshot_to_payload(recovery)
            )
            _validate_episode_snapshot(recovery, payload_text)
            committed_event_id = (
                f"episode.checkpoint.{episode_id}.{version}.{uuid4().hex}"
            )
            self._conn.execute(
                "INSERT INTO episode_snapshots (episode_id, version, "
                "checkpoint_key, source, payload_json, payload_hash, "
                "base_episode_id, base_version, journal_through_seq, "
                "committed_event_id, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    version,
                    checkpoint_key,
                    recovery.source.value,
                    payload_text,
                    payload_hash,
                    recovery.base_snapshot_ref.episode_id
                    if recovery.base_snapshot_ref
                    else None,
                    recovery.base_snapshot_ref.version
                    if recovery.base_snapshot_ref
                    else None,
                    recovery.journal_through_seq,
                    committed_event_id,
                    _now_iso(),
                ),
            )
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_CHECKPOINT_COMMITTED,
                    episode_id,
                    {
                        "version": version,
                        "source": recovery.source.value,
                        "payload_hash": payload_hash,
                        "journal_through_seq": recovery.journal_through_seq,
                        "committed_event_id": committed_event_id,
                        "recovery_reason": reason,
                        "new_status": ep.status.value,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                    event_id=committed_event_id,
                )
            )
            return SnapshotRef(
                episode_id=episode_id,
                version=version,
                committed_event_id=committed_event_id,
                payload_hash=payload_hash,
            )

    def _recover_ownership_in_tx(
        self,
        episode_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None,
    ) -> Lease:
        """Take over an Episode's ownership lease INSIDE the caller's tx.

        codex H5: recovering a live Episode is a ``LeaseConflict`` — the
        existing active lease MUST be expired (or absent). Mirrors
        ``_grant_episode_ownership_in_tx`` but takes over an expired grant:
        mark the old row released (history preserved), bump the fence counter,
        INSERT a fresh row with a higher token. The new owner can then write a
        fenced RECOVERING transition in the same transaction."""

        assert self._conn is not None
        now_str = _now_iso()
        expires_str = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type='episode_ownership' "
                "AND resource_id=? AND idempotency_key=? AND released_at IS NULL",
                (episode_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise LeaseConflict("episode_ownership", episode_id)
                # codex R3-M3: do not hand back an already-expired lease — the
                # caller would fail the first fenced write. A retry that lands
                # after TTL is a conflict (the caller must re-acquire fresh).
                if existing["expires_at"] <= now_str:
                    raise LeaseConflict("episode_ownership", episode_id)
                return _lease_from_row(existing)
        active = self._conn.execute(
            "SELECT * FROM leases WHERE resource_type='episode_ownership' "
            "AND resource_id=? AND released_at IS NULL",
            (episode_id,),
        ).fetchone()
        if active is not None and active["expires_at"] > now_str:
            raise LeaseConflict("episode_ownership", episode_id)
        if active is not None:
            self._conn.execute(
                "UPDATE leases SET released_at=? WHERE lease_id=?",
                (now_str, active["lease_id"]),
            )
        token = self._next_fence_token_in_tx(
            self._EPISODE_OWNERSHIP_RESOURCE_TYPE, episode_id
        )
        lease_id = uuid4().hex
        try:
            self._conn.execute(
                "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                "acquired_at, expires_at, idempotency_key, released_at, "
                "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    lease_id,
                    self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
                    episode_id,
                    owner,
                    now_str,
                    expires_str,
                    idempotency_key,
                    token,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # codex R3-H3: surface a collision as LeaseConflict, never a raw
            # sqlite3 error (idempotency_key still in use, or another writer
            # raced the grant/takeover).
            raise LeaseConflict("episode_ownership", episode_id) from exc
        self._insert_event_in_tx(
            EventEnvelope(
                event_id=f"episode.ownership_acquired.{uuid4().hex}",
                kind=EventKind.EPISODE_OWNERSHIP_ACQUIRED,
                occurred_at=now_str,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=self._policy_version,
                payload={
                    "lease_id": lease_id,
                    "owner": owner,
                    "fencing_token": token,
                    "expires_at": expires_str,
                    "recovery": True,
                },
                episode_id=episode_id,
            )
        )
        return Lease(
            lease_id=lease_id,
            resource_type=self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
            resource_id=episode_id,
            owner=owner,
            acquired_at=now_str,
            expires_at=expires_str,
            idempotency_key=idempotency_key,
            fencing_token=token,
        )

    def recover_episode(
        self,
        episode_id: str,
        *,
        new_owner: str,
        ttl_seconds: int,
        idempotency_key: str,
        reason: str,
    ) -> Lease:
        """Take over an Episode whose ownership lease expired.

        Atomic in ONE transaction (codex H5): verify the Episode exists and is
        non-terminal, take over the expired lease (higher fencing token), then
        write a fenced EPISODE_RECOVERING transition with the fresh token. The
        caller then decides whether to ``checkpoint_recovery_partial`` + close,
        or to resume.

        The previous implementation split this across two transactions
        (``acquire_episode_ownership`` committed, then a second tx wrote
        RECOVERING); a crash between them left an orphan lease on a missing or
        terminal Episode. Everything now shares one IMMEDIATE tx, so any
        failure rolls the lease back too.
        """

        assert self._conn is not None
        if not new_owner or ttl_seconds <= 0:
            raise EpisodeCommandError("new_owner and positive ttl required")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise EpisodeCommandError("idempotency_key must be a non-empty string")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} is terminal ({ep.status.value}); "
                    f"cannot recover"
                )
            lease = self._recover_ownership_in_tx(
                episode_id, new_owner, ttl_seconds, idempotency_key
            )
            if ep.status != EpisodeStatus.RECOVERING:
                self._fenced_status_change_in_tx(
                    episode_id=episode_id,
                    kind=EventKind.EPISODE_RECOVERING,
                    new_status=EpisodeStatus.RECOVERING,
                    expected_lease_id=lease.lease_id,
                    expected_owner=new_owner,
                    expected_token=lease.fencing_token,
                    extra_payload={"reason": reason},
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                )
        return lease

    def resume_recovered_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """RECOVERING → ACTIVE (fenced). The new owner resumes the recovered
        Episode in place.

        codex N3 (round 2): previously ``RECOVERING`` had no exit command, so a
        recovered Episode was stuck (``close_episode`` only accepts
        CHECKPOINTING, ``activate_suspended_episode`` only accepts
        SUSPENDED_READY). This command is the "新 owner 继续" branch of spec
        line 165. It runs AFTER ``checkpoint_recovery_partial`` has committed a
        recovery snapshot.

        Composite (Task-bound): claim foreground (CAS, ``ForegroundConflict``
        if another Task holds it), ensure the Task is RUNNING and the WorkItem
        is RUNNING. system WorkItem: ensure the WorkItem is RUNNING. Then the
        fenced RECOVERING → ACTIVE transition. What survived the crash (Task
        RUNNING, foreground already claimed) is left as-is; only inconsistencies
        are fixed up."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.RECOVERING:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be RECOVERING to resume "
                    f"(got {ep.status.value})"
                )
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            now = _now_iso()
            if ep.task_id is not None:
                task = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} references unknown task "
                        f"{ep.task_id!r}"
                    )
                # codex R3-M1: gate the Task source state. RUNNING (survived
                # the crash, foreground still claimed) and READY are the legit
                # resume sources; anything else (e.g. waiting_user from a
                # pre-crash reconcile) must be resolved first, not silently
                # cleared by an unconditional move to RUNNING.
                if task.status not in (
                    TaskStatus.READY,
                    TaskStatus.RUNNING,
                ):
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be READY or RUNNING to resume "
                        f"a recovered Episode (got {task.status.value})"
                    )
                current_fg = self._read_foreground_task_id()
                if current_fg is not None and current_fg != ep.task_id:
                    raise ForegroundConflict(current_fg)
                if current_fg is None:
                    cur = self._conn.execute(
                        "UPDATE foreground_claim SET task_id=? WHERE id=1 "
                        "AND task_id IS NULL",
                        (ep.task_id,),
                    )
                    if (
                        cur.rowcount != 1
                        and self._read_foreground_task_id() != ep.task_id
                    ):
                        raise ForegroundConflict(self._read_foreground_task_id())
                    self._insert_event_in_tx(
                        self._make_task_event(
                            EventKind.FOREGROUND_CLAIMED,
                            ep.task_id,
                            {"task_id": ep.task_id},
                        )
                    )
                if task.status == TaskStatus.READY:
                    # READY → RUNNING (RUNNING survived the crash; no event)
                    self._insert_event_in_tx(
                        self._make_task_event(
                            EventKind.TASK_STATUS_CHANGED,
                            ep.task_id,
                            {"new_status": TaskStatus.RUNNING.value},
                        )
                    )
            if (
                work_item is not None
                and work_item.status
                in (WorkItemStatus.READY, WorkItemStatus.SUSPENDED)
            ):
                # codex R3-L3: whitelist the resumable WorkItem source states
                # (mirror activate_suspended_episode) rather than moving a
                # WorkItem in any odd state to RUNNING.
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.RUNNING,
                        ep.task_id,
                        now,
                    )
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_ACTIVATED,
                new_status=EpisodeStatus.ACTIVE,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"recovery_resume": True},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )

    def close_recovered_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """RECOVERING → CLOSED (fenced, terminal). The "close after recovery"
        branch of spec line 165.

        codex N3 (round 2): ``close_episode`` only accepts CHECKPOINTING, so a
        recovered Episode could not be closed even after
        ``checkpoint_recovery_partial`` committed its recovery snapshot. This
        command closes from RECOVERING, requiring that a recovery snapshot was
        committed (``last_snapshot_ref`` is set) so 090 can start fresh from it.
        Releases the ownership lease in the same tx."""

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.RECOVERING:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be RECOVERING to close after "
                    f"recovery (got {ep.status.value})"
                )
            if ep.last_snapshot_ref is None:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} has no recovery snapshot; run "
                    f"checkpoint_recovery_partial before close_recovered_episode"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_CLOSED,
                new_status=EpisodeStatus.CLOSED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"recovery_close": True},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )
            row = self._read_episode_lease_row(episode_id)
            if row is not None and row["lease_id"] == expected_lease_id:
                self._conn.execute(
                    "UPDATE leases SET released_at=? WHERE lease_id=?",
                    (_now_iso(), expected_lease_id),
                )

    # ------------------------------------------------------------- side effects

    def record_side_effect(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        action_ref: str,
        idempotency_key: str,
        outcome: str,
        evidence_ref: str | None = None,
        description: str = "",
    ) -> None:
        """Record an external side effect (fenced).

        ``outcome='done'`` requires ``evidence_ref`` (confirmed result).
        ``outcome='unknown_requires_reconcile'`` means the action may have
        happened but the result was not written back; an 084
        ``SIDE_EFFECT_UNCONFIRMED`` event is also appended so the reducer
        tracks it as an ``UnknownAction`` awaiting reconciliation. Replay is
        forbidden until reality is checked.

        Idempotent on ``(action_ref, idempotency_key)`` (codex R3-M4): the
        ``idempotency_key`` parameter is the caller's retry identity for THIS
        action; a crash-retry that already landed returns without appending a
        second ``side_effect_recorded`` event or a duplicate UnknownAction.
        """

        assert self._conn is not None
        if outcome not in ("done", "unknown_requires_reconcile"):
            raise EpisodeCommandError(
                f"outcome must be 'done' or 'unknown_requires_reconcile' "
                f"(got {outcome!r})"
            )
        if outcome == "done" and not evidence_ref:
            raise EpisodeCommandError("done side effect requires evidence_ref")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} is terminal ({ep.status.value})"
                )
            # codex R3-M4: idempotent on (action_ref, idempotency_key). A retry
            # that already persisted this action is a no-op (do not append a
            # second side_effect_recorded event or a duplicate UnknownAction).
            already = self._conn.execute(
                "SELECT 1 FROM events "
                "WHERE kind='episode.side_effect_recorded' AND episode_id=? "
                "AND json_extract(payload, '$.action_ref')=? "
                "AND json_extract(payload, '$.idempotency_key')=?",
                (episode_id, action_ref, idempotency_key),
            ).fetchone()
            if already is not None:
                return
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_SIDE_EFFECT_RECORDED,
                    episode_id,
                    {
                        "action_ref": action_ref,
                        "idempotency_key": idempotency_key,
                        "outcome": outcome,
                        "evidence_ref": evidence_ref,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                )
            )
            if outcome == "unknown_requires_reconcile":
                self._insert_event_in_tx(
                    EventEnvelope(
                        event_id=f"side_effect.unconfirmed.{uuid4().hex}",
                        kind=EventKind.SIDE_EFFECT_UNCONFIRMED,
                        occurred_at=_now_iso(),
                        source="kernel",
                        provenance=Provenance.MACHINE_OBSERVATION,
                        policy_version=self._policy_version,
                        payload={
                            "action_ref": action_ref,
                            "idempotency_key": idempotency_key,
                            "description": description,
                        },
                        work_item_id=ep.work_item_id,
                        task_id=ep.task_id,
                        episode_id=episode_id,
                    )
                )

    def read_snapshot(self) -> Snapshot:
        """Return the current derived snapshot (replay + live leases + foreground).

        ``schema_version`` is already populated by ``replay`` (via
        ``initial_snapshot``); ``active_leases`` and ``foreground_task_id``
        are merged in from live tables here — both are operational state, not
        audit (slice-086: foreground owner lives in foreground_claim, not
        derived from FOREGROUND_CLAIMED events).

        All three reads run inside one DEFERRED read transaction (``_read_tx``)
        so a concurrent claim/release commit cannot split them and return an
        inconsistent snapshot (codex review HIGH 5).
        """

        with self._read_tx():
            snap = self.replay()
            return replace(
                snap,
                active_leases=self._read_active_leases(),
                foreground_task_id=self._read_foreground_task_id(),
            )
