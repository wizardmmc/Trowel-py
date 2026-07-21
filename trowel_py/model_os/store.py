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
    Snapshot,
    TaskState,
    initial_snapshot,
    reduce_event,
)
from trowel_py.model_os.types import (
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    Task,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)

_SCHEMA_VERSION = 1
_DEFAULT_POLICY_VERSION = "v0"

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
    payload_hash TEXT
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
    released_at TEXT
);

-- at most one ACTIVE lease per resource (CAS primitive)
CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_active
    ON leases(resource_type, resource_id) WHERE released_at IS NULL;

-- idempotency: the same key reclaims the same active lease
CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_idem
    ON leases(idempotency_key) WHERE idempotency_key IS NOT NULL;

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
    "cause_id, correlation_id, outcome, payload, payload_hash) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
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
        version never enters SQL via string substitution.
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
        """Atomically claim a lease (compare-and-set).

        Returns the held lease on success. Raises ``LeaseConflict`` if
        another active, unexpired lease already owns the resource. An expired
        lease is taken over atomically. Re-claiming with the same
        ``idempotency_key`` AND the same owner returns the original lease;
        the same key under a DIFFERENT owner is treated as a conflict (the
        key is the caller's retry identity, not a transferable handle).
        """

        assert self._conn is not None
        now_str = _now_iso()
        expires_str = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()

        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM leases WHERE idempotency_key=? AND released_at IS NULL",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise LeaseConflict(resource_type, resource_id)
                return _lease_from_row(existing)

        lease_id = uuid4().hex
        try:
            with self._tx():
                self._conn.execute(
                    "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                    "acquired_at, expires_at, idempotency_key, released_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        lease_id,
                        resource_type,
                        resource_id,
                        owner,
                        now_str,
                        expires_str,
                        idempotency_key,
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
        expired lease, or raise ``LeaseConflict``."""

        assert self._conn is not None
        if idempotency_key is not None:
            row = self._conn.execute(
                "SELECT * FROM leases WHERE idempotency_key=? AND released_at IS NULL",
                (idempotency_key,),
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
        if existing is not None and existing["expires_at"] < now_str:
            new_lease_id = uuid4().hex
            with self._tx():
                cur = self._conn.execute(
                    "UPDATE leases SET lease_id=?, owner=?, acquired_at=?, "
                    "expires_at=?, idempotency_key=? "
                    "WHERE resource_type=? AND resource_id=? AND released_at IS NULL "
                    "AND expires_at < ?",
                    (
                        new_lease_id,
                        owner,
                        now_str,
                        expires_str,
                        idempotency_key,
                        resource_type,
                        resource_id,
                        now_str,
                    ),
                )
                if cur.rowcount == 1:
                    return Lease(
                        lease_id=new_lease_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        owner=owner,
                        acquired_at=now_str,
                        expires_at=expires_str,
                        idempotency_key=idempotency_key,
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
        same ``event_id`` does not duplicate the row and returns the original
        seq.

        slice-086: Task lifecycle kinds (task.created / status_changed /
        completed / ...) are refused — those must go through the structured
        Task commands so the gates (USER_REQUEST done requires USER_DECISION,
        foreground ⇔ running, etc.) cannot be bypassed by a caller that
        happens to hold a Store handle (codex review HIGH 1).
        """

        assert self._conn is not None
        if event.kind in _TASK_LIFECYCLE_KINDS:
            raise TaskCommandError(
                f"event kind {event.kind!r} is a Task lifecycle event; use "
                f"the corresponding structured command (slice-086 grill "
                f"decision 5: provenance is not an authorisation mechanism)"
            )
        payload_text, payload_hash = _payload_json(event.payload)
        try:
            with self._tx():
                self._conn.execute(
                    _EVENT_INSERT_SQL,
                    _event_params(event, payload_text, payload_hash),
                )
        except sqlite3.IntegrityError:
            # duplicate event_id — idempotent replay, return existing seq
            pass
        row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        return int(row["seq"])

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
            except BaseException:
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
            return None  # duplicate event_id — idempotent
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
        task_id: str,
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
        """Clear foreground_claim + emit FOREGROUND_RELEASED audit event.

        Caller holds the transaction. No-op if this task isn't foreground.
        """

        assert self._conn is not None
        self._conn.execute("UPDATE foreground_claim SET task_id=NULL WHERE id=1")
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
        """Shared body for set_waiting_user / _event / _incubating: release
        foreground if held, suspend WorkItem, set waiting_condition + status.

        Source state must be RUNNING (the frozen graph is running→waiting_*);
        a backlog/ready Task cannot leap into waiting (codex review HIGH 2)."""

        assert self._conn is not None
        with self._tx():
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
