"""Frozen domain types for the Model OS journal (slice-084).

Everything here is a plain value object: enums and frozen dataclasses. No
behaviour, no I/O. The reducer (``reducer.py``) and the store (``store.py``)
build on these.

Design notes:
- ``Provenance`` is an enum with a frozen strength ordering. The reducer
  refuses to let a weaker provenance overwrite a stronger one's claim — this
  is the concrete mechanism for the spec invariant "machine_observation /
  user_decision / model_hypothesis / unknown / stale 不允许静默升级".
- ``EventKind`` is deliberately a class of ``str`` constants, NOT an enum:
  the reducer must survive encountering kinds invented by a future version
  (spec: "未知新事件可保留但不能破坏旧 reducer"). An enum would force every
  new kind through a migration.
- ``EventEnvelope.payload`` is the caller-provided dict; the store redacts it
  before persisting (see ``redaction.py``). The reducer treats payload as
  read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


# ----------------------------------------------------------------------- provenance


class Provenance(str, Enum):
    """Where a recorded fact came from, ranked by trust strength.

    Strength is the rule the reducer uses to enforce "no silent upgrade":
    a fact asserted by a weaker source cannot overwrite the same fact
    asserted by a stronger source. Order (high → low trust):

    ``user_decision`` > ``machine_observation`` > ``model_hypothesis``
    > ``unknown`` > ``stale``.
    """

    USER_DECISION = "user_decision"
    MACHINE_OBSERVATION = "machine_observation"
    MODEL_HYPOTHESIS = "model_hypothesis"
    UNKNOWN = "unknown"
    STALE = "stale"

    @property
    def strength(self) -> int:
        """Return the frozen trust rank used by the reducer (higher = stronger)."""

        order = {
            Provenance.USER_DECISION: 4,
            Provenance.MACHINE_OBSERVATION: 3,
            Provenance.MODEL_HYPOTHESIS: 2,
            Provenance.UNKNOWN: 1,
            Provenance.STALE: 0,
        }
        return order[self]


# ----------------------------------------------------------------------- work item


class WorkItemKind(str, Enum):
    """What kind of scheduled work a WorkItem represents.

    Task work references a Task; default/maintenance/experiment use the
    system owner; incubation references the original Task. Episode may only
    bind to a WorkItem, never invent a parallel execution identity.
    """

    TASK = "task"
    DEFAULT = "default"
    MAINTENANCE = "maintenance"
    EXPERIMENT = "experiment"
    INCUBATION = "incubation"


class WorkItemStatus(str, Enum):
    """Lifecycle of a WorkItem (subset relevant to the journal spine)."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUSPENDED = "suspended"
    DONE = "done"
    CANCELLED = "cancelled"


class SessionPurpose(str, Enum):
    """Why a native session was opened (architecture: ``session_purpose``).

    OS-generated default/incubation/maintenance/experiment sessions default to
    memory-ineligible; only explicit adoption opens the M6 Memory channel.
    """

    FOREGROUND = "foreground"
    DEFAULT = "default"
    INCUBATION = "incubation"
    MAINTENANCE = "maintenance"
    EXPERIMENT = "experiment"


class MemoryEligibility(str, Enum):
    """Whether a session's transcript may enter M6 Memory/Profile."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    ADOPTED = "adopted"


@dataclass(frozen=True)
class WorkItem:
    """A scheduled unit of work (slice-084 creates the record; later slices
    drive its lifecycle).

    Attributes:
        work_item_id: stable id (store-assigned).
        kind: discriminates Task vs system work.
        owner_ref: who owns this work — "system" for default/maintenance/
            experiment, the task owner for Task work.
        task_id: present for Task and incubation work; ``None`` for pure
            system work.
        status: current lifecycle position.
        session_purpose: the native session purpose this work carries.
        memory_eligibility: whether its transcript may enter M6 Memory.
        created_at: ISO-8601 UTC creation timestamp.
    """

    work_item_id: str
    kind: WorkItemKind
    owner_ref: str
    task_id: str | None
    status: WorkItemStatus
    session_purpose: SessionPurpose
    memory_eligibility: MemoryEligibility
    created_at: str


# ----------------------------------------------------------------------- events


class EventKind:
    """Known journal event kinds.

    Stored as ``str`` (not Enum) so the reducer can retain unknown future
    kinds without crashing. The reducer matches on these constants; anything
    else lands in ``Snapshot.unrecognized_event_kinds``.
    """

    WORK_ITEM_CREATED = "work_item.created"
    WORK_ITEM_STATUS_CHANGED = "work_item.status_changed"
    SIDE_EFFECT_UNCONFIRMED = "side_effect.unconfirmed"
    PENDING_CHANNEL_LOST = "pending_channel.lost"
    NOTE = "note"
    #: slice-085 — a model's proposed Self change. Recorded for audit but the
    #: reducer NEVER applies it: Self is assembled from runtime facts in
    #: ``self_assembler``, not derived from events. This is the structural
    #: anti-forgery (pass 4): no event, regardless of provenance, can alter
    #: Self state.
    SELF_CHANGE_PROPOSED = "self.change_proposed"


@dataclass(frozen=True)
class EventEnvelope:
    """An immutable journal event.

    ``payload`` is redacted by the store before it ever touches SQLite; the
    reducer reads it as-is. ``outcome`` distinguishes normal events from
    "may have happened, result not written back" (requires_reconcile) and
    "pending control channel lost on restart" (requires_user_restart, per
    spike-083).
    """

    event_id: str
    kind: str
    occurred_at: str
    source: str
    provenance: Provenance
    policy_version: str
    payload: dict[str, Any]
    work_item_id: str | None = None
    task_id: str | None = None
    episode_id: str | None = None
    native_session_id: str | None = None
    cause_id: str | None = None
    correlation_id: str | None = None
    outcome: str | None = None


@dataclass(frozen=True)
class DecisionRecord:
    """An automatic decision recorded BEFORE its command executes.

    Carries the signals seen, the options considered, the choice, the reason
    and the budget change, plus ``policy_version`` so the same event replayed
    under different policies can be explained.
    """

    decision_id: str
    kind: str
    decided_at: str
    signals: dict[str, Any]
    candidates: list[Any]
    choice: str
    reason: str
    policy_version: str
    budget_before: dict[str, Any] | None = None
    budget_after: dict[str, Any] | None = None
    work_item_id: str | None = None
    task_id: str | None = None
    episode_id: str | None = None
    cause_id: str | None = None
    correlation_id: str | None = None


# ----------------------------------------------------------------------- lease


@dataclass(frozen=True)
class Lease:
    """An ownership claim on a resource (CAS primitive).

    ``resource_type``/``resource_id`` identify what is locked; ``owner`` is
    who holds it. Acquisition is compare-and-set: two concurrent claims on
    the same resource yield exactly one winner.
    """

    lease_id: str
    resource_type: str
    resource_id: str
    owner: str
    acquired_at: str
    expires_at: str
    idempotency_key: str | None = None


# ----------------------------------------------------------------------- self manifest


class SubsystemState(str, Enum):
    """Whether a Trowel subsystem's content is injected into this session.

    v0 only distinguishes INJECTED (content loaded into the prompt) from
    OFF (the subsystem exists in the build but its content is not injected
    this call). Later states — e.g. DEGRADED when a subsystem is failing —
    can extend this without rewriting call sites. OFF must NOT be read as
    "the subsystem does not exist": the system exists, only the content is
    absent (slice-085 invariant: off leaks neither memory root nor profile
    body).
    """

    INJECTED = "injected"
    OFF = "off"


@dataclass(frozen=True)
class SelfManifest:
    """Trowel's self-representation: stable identity + dynamic capability.

    Two layers (architecture.md §Self 与身体图式):
    - Stable identity (``identity`` / ``version`` / ``continuity_note``)
      never changes within a version, so a prompt-cache prefix built on it
      stays stable across sessions.
    - Dynamic capability reflects THIS native session's effective runtime,
      model, subsystem injection states and authorization scope — derived
      from the Session Hub binding + runtime facts at assemble time.

    The model can read a rendered Manifest but has no Store write-path to
    alter it (slice-085 pass 4 — structural anti-forgery: no entry point
    means no way to forge). ``task_id`` / ``episode_id`` /
    ``native_session_id`` are location pointers only; they default to
    ``None`` so a Manifest is legal before 090's EpisodeContext attaches
    them.

    ``model`` / ``effort`` are ``None`` when the host has not reported an
    effective value — ``None`` is the explicit "unknown" marker and MUST
    NOT be papered over with a stale cached value (slice-083 frozen
    semantics: cc effective effort has no machine echo).

    Attributes:
        identity: stable name ("Trowel").
        version: self-manifest version, bumped by humans on upgrade.
        continuity_note: the continuous-subject prompt
            ("本次行动是持续主体的一段活动").
        runtime: which native host this session runs on ("cc" | "codex").
        model: effective model once the host reports one; ``None`` = unknown.
        effort: reasoning-effort override; ``None`` = unknown.
        subsystems: Trowel-level subsystem tags present in this build
            (memory / profile / model_os / dual_runtime / todo_loop). These
            are Trowel's body schema, NOT the native runtime's tool/MCP
            roster (that comes from cc/codex themselves).
        memory_state: whether memory content is injected this call.
        profile_state: whether profile content is injected this call.
        native_tools_note: note that native tools/MCP come from cc/codex,
            not duplicated at the Trowel layer.
        authorization_scope: scope of actions needing approval.
        task_id: optional Task pointer (location only, set by 090).
        episode_id: optional Episode pointer (location only, set by 090).
        native_session_id: optional native session pointer (location only).
    """

    identity: str
    version: str
    continuity_note: str
    runtime: str
    model: str | None
    effort: str | None
    subsystems: tuple[str, ...]
    memory_state: SubsystemState
    profile_state: SubsystemState
    native_tools_note: str
    authorization_scope: str
    task_id: str | None = None
    episode_id: str | None = None
    native_session_id: str | None = None
