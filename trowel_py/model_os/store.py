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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dataclasses import replace

from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.reducer import (
    Snapshot,
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
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)

_SCHEMA_VERSION = 1
_DEFAULT_POLICY_VERSION = "v0"

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
"""


class LeaseConflict(Exception):
    """Raised when a CAS lease claim loses to another active owner."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(
            f"lease already held: resource_type={resource_type} resource_id={resource_id}"
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
    ) -> None:
        """Remember the db path and policy version; call ``open()`` to connect.

        Args:
            db_path: path to the model_os.db file (created on first open).
            policy_version: recorded on every event/decision so replay can
                explain why a new policy would decide differently.
        """

        self._path = Path(db_path)
        self._policy_version = policy_version
        self._conn: sqlite3.Connection | None = None

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
        """

        conn = sqlite3.connect(str(self._path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
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
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)),
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
        """

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
            with self._conn:
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
            with self._conn:
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
        with self._conn:
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
        """

        assert self._conn is not None
        payload_text, payload_hash = _payload_json(event.payload)
        try:
            with self._conn:
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
            with self._conn:
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
            with self._conn:
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

    def read_snapshot(self) -> Snapshot:
        """Return the current derived snapshot (replay + live leases).

        ``schema_version`` is already populated by ``replay`` (via
        ``initial_snapshot``); only ``active_leases`` needs to be merged in
        from the live table here.
        """

        snap = self.replay()
        return replace(snap, active_leases=self._read_active_leases())
