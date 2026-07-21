"""Model OS kernel backbone (Milestone 8).

This package is the single durable source of truth for the Model OS: a
transactional SQLite store, an append-only event/decision journal, a pure
reducer that derives snapshots, payload redaction (slice-084), a Self manifest
assembled from runtime facts (slice-085), and a Task pool with warm/foreground
constraints (slice-086). Later slices (Episode runner, Scheduler, Router) build
on top of this spine.

Scope of slice-084: Store + WorkItem + journal + reducer + redaction.
Scope of slice-085: Self Manifest + anti-forgery (no Store write-path).
Scope of slice-086: Task entity + primary WorkItem + warm pool + durable
foreground claim + structured command gate (no MODEL_HYPOTHESIS creation;
USER_REQUEST completion requires USER_DECISION).

Out of scope: Episode driving (087), Scheduler (091), Router (094), UI (101).
"""

from trowel_py.model_os.reducer import (
    Snapshot,
    TaskState,
    WorkItemState,
    initial_snapshot,
    reduce_decision,
    reduce_event,
)
from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.store import (
    ForegroundConflict,
    LeaseConflict,
    ModelOsStore,
    TaskCommandError,
    WarmFull,
)
from trowel_py.model_os.types import (
    CompletionEvidence,
    DecisionRecord,
    ErrorRecord,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    Provenance,
    SelfManifest,
    SessionPurpose,
    SubsystemState,
    Task,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)

__all__ = [
    "CompletionEvidence",
    "DecisionRecord",
    "ErrorRecord",
    "EventEnvelope",
    "EventKind",
    "ForegroundConflict",
    "Lease",
    "LeaseConflict",
    "MemoryEligibility",
    "ModelOsStore",
    "Provenance",
    "SelfManifest",
    "SessionPurpose",
    "SubsystemState",
    "Snapshot",
    "Task",
    "TaskCommandError",
    "TaskOrigin",
    "TaskState",
    "TaskStatus",
    "WaitingCondition",
    "WarmFull",
    "WorkItem",
    "WorkItemKind",
    "WorkItemStatus",
    "WorkItemState",
    "initial_snapshot",
    "redact_payload",
    "reduce_decision",
    "reduce_event",
]
