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
    #: slice-086 — task-level failure. The primary WorkItem of a Task in
    #: ``error`` enters FAILED: it neither finished nor was abandoned, it
    #: broke in a way that retrying the same work will not fix (dependency
    #: overturned, state corruption, etc.). Distinct from a transient
    #: Episode/tool failure, which returns the WorkItem to READY for retry.
    FAILED = "failed"


# ----------------------------------------------------------------------- task
#
# slice-086 introduces the Task entity. A Task is a long-lived objective
# independent of any native session; its primary execution identity is a
# WorkItem(kind=TASK, task_id=this task). See slice-086 spec §概念定位 for
# the four-layer identity split (Task / WorkItem / Episode / Native Session).


class TaskOrigin(str, Enum):
    """Who a Task belongs to — drives who must confirm completion and who is
    notified on error (slice-086 grill decision 11).

    - ``USER_REQUEST``: a human asked for this. ``done`` requires
      ``USER_DECISION`` confirmation; ``error`` prompts the human.
    - ``SELF_INITIATED``: Trowel spawned it (e.g. maintenance, default-state
      follow-up). ``error`` is recorded for later review, not auto-recovered.
    - ``ADOPTED_CANDIDATE``: promoted from a default/incubation candidate via
      explicit adoption. The adoption event reference is mandatory. The
      ``AdoptCandidate`` command path is reserved in v0 (idea_candidates land
      in slice-097); MODEL_HYPOTHESIS can never create a Task directly.
    """

    USER_REQUEST = "user_request"
    SELF_INITIATED = "self_initiated"
    ADOPTED_CANDIDATE = "adopted_candidate"


class TaskStatus(str, Enum):
    """Lifecycle of a Task (slice-086 spec §TaskStatus 状态机).

    Legal transitions (anything else is rejected by the command layer):

    ::

        backlog → ready → running
        running → waiting_user | waiting_event | incubating | ready | done | cancelled | error
        waiting_user   → ready
        waiting_event  → ready          # condition matcher is slice-095
        incubating     → ready          # review/reframe is slice-098/099
        warm Task      → backlog        # demotion (user-decided)
        any non-terminal → cancelled
        any non-terminal → error        # task-level failure only

    ``done`` / ``cancelled`` / ``error`` are terminal: no auto-recovery. To
    resume after ``error``, reopen via the normal 087/090 path using
    ``ErrorRecord.last_snapshot_ref`` — the Task does not flip back to ready
    on its own (Temporal workflow-failure semantics: retrying the same logic
    does not fix a code-bug or overturned dependency).
    """

    BACKLOG = "backlog"
    READY = "ready"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_EVENT = "waiting_event"
    INCUBATING = "incubating"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        """True for done / cancelled / error — states with no outgoing edge."""

        return self in (TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.ERROR)


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
    # slice-086 — Task lifecycle. Tasks are event-sourced like work_items;
    # the reducer folds these into TaskState. The foreground claim pair
    # (FOREGROUND_CLAIMED / RELEASED) is audit-only: the live foreground
    # owner lives in the foreground_claim table (read at snapshot time,
    # same pattern as active_leases — not derived from events).
    TASK_CREATED = "task.created"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_CONSTRAINT_APPENDED = "task.constraint_appended"
    TASK_WARM_CHANGED = "task.warm_changed"
    TASK_WARM_RANK_SET = "task.warm_rank_set"
    TASK_WAITING_SET = "task.waiting_set"
    TASK_WAITING_CLEARED = "task.waiting_cleared"
    TASK_AUTHORIZATION_CHANGED = "task.authorization_changed"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"
    TASK_ERROR_RECORDED = "task.error_recorded"
    TASK_CREATION_DENIED = "task.creation_denied"
    FOREGROUND_CLAIMED = "foreground.claimed"
    FOREGROUND_RELEASED = "foreground.released"


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


# ----------------------------------------------------- task-shaped payloads ---
#
# These frozen dataclasses are shapes used inside Task and inside event
# payloads (``TASK_WAITING_SET`` / ``TASK_COMPLETED`` / ``TASK_ERROR_RECORDED``).
# Kept here so the reducer, the store and tests share one canonical shape.


@dataclass(frozen=True)
class WaitingCondition:
    """Why a Task paused and what it is waiting for (slice-086 §WaitingCondition).

    ``kind`` discriminates the three waiting states. All variants carry a
    human-readable ``cause`` (the spec mandates "waiting 必须保存可理解的原因").
    Formal matching (predicate evaluation, review scheduling) is implemented
    in slice-095/098 — slice-086 only stores the structure and validates the
    invariants below.

    Invariants by kind:
    - ``waiting_user``: ``cause`` required; ``correlation_id`` links back to
      the user input that will resume the Task.
    - ``waiting_event``: ``condition_kind`` + ``target_ref`` required (the
      external predicate). Matcher is slice-095.
    - ``incubating``: ``open_question`` AND ``preparation_snapshot_ref`` both
      required (architecture.md §默认态与孵化: "必须先有准备 snapshot 和明确未解
      问题"). Review/reframe is slice-098/099.
    """

    kind: str
    cause: str
    correlation_id: str | None = None
    deadline: str | None = None
    # waiting_event only
    condition_kind: str | None = None
    target_ref: str | None = None
    match_params: dict[str, Any] | None = None
    # incubating only
    open_question: str | None = None
    preparation_snapshot_ref: str | None = None
    earliest_review_at: str | None = None


@dataclass(frozen=True)
class CompletionEvidence:
    """Who confirmed a Task done and on what basis (slice-086 §CompletionEvidence).

    The spec mandates: ``done`` must record confirmer + evidence; model
    self-report is not sufficient on its own. For ``USER_REQUEST`` tasks,
    ``confirmation_provenance`` MUST be ``USER_DECISION`` — the store rejects
    a model-claimed done on a human task.
    """

    confirmed_by: str
    confirmation_provenance: Provenance
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErrorRecord:
    """Why a Task entered ``error`` and where its work现场 lives (slice-086
    §ErrorRecord).

    ``error`` is terminal and never auto-recovered (Temporal workflow-failure
    semantics). To reopen, a later command follows the normal 087/090 path
    using ``last_snapshot_ref`` — the Task does not flip back to ready on its
    own. ``origin`` records who the Task belongs to so the kernel knows whether
    to prompt a human (USER_REQUEST) or just record for review (SELF_INITIATED).
    """

    origin: TaskOrigin
    failure_reason: str
    last_episode_ref: str | None = None
    last_snapshot_ref: str | None = None
    recovery_hint: str | None = None


@dataclass(frozen=True)
class Task:
    """A long-lived objective tracked across sessions (slice-086).

    A Task is NOT a native session and NOT a single execution. Its primary
    execution identity is one WorkItem(kind=TASK, task_id=this task), created
    atomically with the Task. Later slices may attach INCUBATION WorkItems for
    paused/reframe rounds. The Task owns the business state (goal, constraints,
    waiting, completion, error); the WorkItem owns the scheduling state.

    ``original_goal`` is frozen at creation and NEVER overwritten — later
    corrections are appended via ``appended_constraints`` or an explicit
    reframe event, so the system can always explain where the Task came from.
    """

    task_id: str
    origin: TaskOrigin
    original_goal: str
    appended_constraints: tuple[str, ...]
    status: TaskStatus
    priority: int
    warm: bool
    warm_rank: int | None
    authorization_scope: str
    waiting_condition: WaitingCondition | None
    completion_evidence: CompletionEvidence | None
    error_record: ErrorRecord | None
    primary_work_item_id: str | None
    created_at: str
    updated_at: str
