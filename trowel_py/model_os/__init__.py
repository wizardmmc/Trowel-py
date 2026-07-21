"""Model OS kernel backbone (Milestone 8).

This package is the single durable source of truth for the Model OS: a
transactional SQLite store, an append-only event/decision journal, a pure
reducer that derives snapshots, payload redaction (slice-084), a Self manifest
assembled from runtime facts (slice-085), a Task pool with warm/foreground
constraints (slice-086), and an Episode lifecycle with fencing, suspend/resume
and recovery (slice-087). Later slices (Episode driving, Scheduler, Router)
build on top of this spine.

Scope of slice-084: Store + WorkItem + journal + reducer + redaction.
Scope of slice-085: Self Manifest + anti-forgery (no Store write-path).
Scope of slice-086: Task entity + primary WorkItem + warm pool + durable
foreground claim + structured command gate (no MODEL_HYPOTHESIS creation;
USER_REQUEST completion requires USER_DECISION).
Scope of slice-087: Episode entity + ownership lease with fencing tokens +
cooperative/recovery_partial snapshots + 2-phase suspend/resume +
reconcile_required blocked state. Fencing is enforced by event kind
(_EPISODE_FENCED_KINDS); stale writers are rejected at the store layer.

Out of scope: Episode driving / cooperative yield (089), fresh-session start
(090), Scheduler (091), Router (094), UI (101).
"""

from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.reducer import (
    EpisodeState,
    Snapshot,
    TaskState,
    WorkItemState,
    initial_snapshot,
    reduce_decision,
    reduce_event,
)
from trowel_py.model_os.store import (
    EpisodeCommandError,
    ForegroundConflict,
    LeaseConflict,
    ModelOsStore,
    StaleWriterRejected,
    TaskCommandError,
    WarmFull,
)
from trowel_py.model_os.types import (
    ArtifactRef,
    CompletionEvidence,
    DecisionRecord,
    Episode,
    EpisodeSnapshot,
    EpisodeStatus,
    ErrorRecord,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    PendingDescriptor,
    Provenance,
    ReconcileReason,
    SelfManifest,
    SessionPurpose,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
    SubsystemState,
    Task,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WaitingSubtype,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)

__all__ = [
    "ArtifactRef",
    "CompletionEvidence",
    "DecisionRecord",
    "Episode",
    "EpisodeCommandError",
    "EpisodeSnapshot",
    "EpisodeState",
    "EpisodeStatus",
    "ErrorRecord",
    "EventEnvelope",
    "EventKind",
    "ForegroundConflict",
    "Lease",
    "LeaseConflict",
    "MemoryEligibility",
    "ModelOsStore",
    "PendingDescriptor",
    "Provenance",
    "ReconcileReason",
    "SelfManifest",
    "SessionPurpose",
    "SideEffectRecord",
    "Snapshot",
    "SnapshotRef",
    "SnapshotSource",
    "StaleWriterRejected",
    "SubsystemState",
    "Task",
    "TaskCommandError",
    "TaskOrigin",
    "TaskState",
    "TaskStatus",
    "WaitingCondition",
    "WaitingSubtype",
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
