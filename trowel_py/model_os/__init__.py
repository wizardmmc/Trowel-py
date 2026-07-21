"""Model OS kernel backbone (Milestone 8, slice-084).

This package establishes the single durable source of truth for the Model OS:
a transactional SQLite store, an append-only event/decision journal, a pure
reducer that derives snapshots, and payload redaction. Later slices (Task
pool, Episode runner, Scheduler, Router) build on top of this spine.

Scope of slice-084 (see ``docs/slices/activate/slice-084.md``):
- Store with transactions, CAS/lease, append event/decision, read snapshot,
  replay by seq.
- ``WorkItem`` unifying Task / default / incubation / maintenance execution
  identity.
- ``EventEnvelope`` / ``DecisionRecord`` with provenance, cause/correlation,
  policy version and redacted payload.
- Pure reducer with no-silent-upgrade provenance rules and forward-compat
  handling of unknown event kinds.

Out of scope: Task creation, model calls, UI, M6 migration.
"""

from trowel_py.model_os.reducer import (
    Snapshot,
    WorkItemState,
    initial_snapshot,
    reduce_decision,
    reduce_event,
)
from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.store import LeaseConflict, ModelOsStore
from trowel_py.model_os.types import (
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    Provenance,
    SelfManifest,
    SessionPurpose,
    SubsystemState,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)

__all__ = [
    "DecisionRecord",
    "EventEnvelope",
    "EventKind",
    "Lease",
    "LeaseConflict",
    "MemoryEligibility",
    "ModelOsStore",
    "Provenance",
    "SelfManifest",
    "SessionPurpose",
    "SubsystemState",
    "Snapshot",
    "WorkItem",
    "WorkItemKind",
    "WorkItemStatus",
    "WorkItemState",
    "initial_snapshot",
    "redact_payload",
    "reduce_decision",
    "reduce_event",
]
