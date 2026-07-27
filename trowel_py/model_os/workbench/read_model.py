from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trowel_py.agent_host.hub import SessionHubError
from trowel_py.model_os.automation import automation_is_paused
from trowel_py.model_os.journal import JournalBoundary
from trowel_py.model_os.scheduling.models import ScheduleInput
from trowel_py.model_os.scheduling.policy import decide_schedule
from trowel_py.model_os.scheduling.read_model import build_schedule_input

@dataclass(frozen=True)
class WorkbenchTask:
    task_id: str
    goal: str
    constraints: tuple[str, ...]
    status: str
    priority: int
    warm: bool
    warm_rank: int | None
    is_foreground: bool
    waiting: dict[str, Any] | None
    pending_request: dict[str, Any] | None
    episode_id: str | None
    episode_status: str | None
    agent_session_id: str | None
    runtime: str | None
    model: str | None
    effort: str | None
    connected: bool | None
    running: bool | None
    can_send_message: bool
    current_judgment: str | None
    next_steps: tuple[str, ...]
    updated_at: str


@dataclass(frozen=True)
class WorkbenchRecentEvent:
    stream: str
    stream_seq: int
    entry_id: str
    kind: str
    recorded_at: str
    task_id: str | None
    episode_id: str | None
    outcome: str | None
    reason: str | None


@dataclass(frozen=True)
class WorkbenchState:
    as_of: JournalBoundary
    automation_paused: bool
    foreground_task_id: str | None
    next_task_id: str | None
    tasks: tuple[WorkbenchTask, ...]
    candidates: tuple[dict[str, Any], ...]
    recent_events: tuple[WorkbenchRecentEvent, ...]


def _waiting_payload(waiting) -> dict[str, Any] | None:
    if waiting is None:
        return None
    return {
        "kind": waiting.kind,
        "cause": waiting.cause,
        "subtype": waiting.subtype.value if waiting.subtype is not None else None,
        "episode_id": waiting.episode_id,
        "correlation_id": waiting.correlation_id,
        "deadline": waiting.deadline,
        "condition_kind": waiting.condition_kind,
        "target_ref": waiting.target_ref,
        "open_question": waiting.open_question,
        "earliest_review_at": waiting.earliest_review_at,
    }


def _pending_request_payload(hub, binding, waiting) -> dict[str, Any] | None:
    if (
        hub is None
        or binding is None
        or waiting is None
        or waiting.correlation_id is None
    ):
        return None
    try:
        return hub.pending_request(
            binding.agent_session_id,
            waiting.correlation_id,
        )
    except (KeyError, RuntimeError, SessionHubError, ValueError):
        return None


def _current_episode(snapshot, task_id: str):
    episodes = [
        episode
        for episode in snapshot.episodes
        if episode.task_id == task_id and not episode.status.is_terminal
    ]
    return max(episodes, key=lambda item: item.updated_at) if episodes else None


def _session_facts(store, episode, hub):
    if episode is None:
        return None, None
    binding = store.episode_runtime_binding(episode.episode_id)
    if binding is None:
        return None, None
    session = hub.get(binding.agent_session_id) if hub is not None else None
    return binding, session


def _snapshot_facts(store, episode) -> tuple[str | None, tuple[str, ...]]:
    if episode is None or episode.last_snapshot_ref is None:
        return None, ()
    try:
        snapshot = store.read_episode_snapshot(episode.last_snapshot_ref)
    except (KeyError, ValueError):
        return None, ()
    return snapshot.current_judgment, snapshot.next_steps


def _next_task_id(store, current: str | None, override: str | None) -> str | None:
    schedule_input = build_schedule_input(
        store,
        trigger_event_ref="workbench.read",
        user_override_task_id=override,
    )
    if override == current:
        return None
    queued = ScheduleInput(
        trigger_event_ref=schedule_input.trigger_event_ref,
        journal_boundary=schedule_input.journal_boundary,
        candidates=schedule_input.candidates,
        current_foreground_task_id=None,
        previous_foreground_task_id=schedule_input.previous_foreground_task_id,
        user_override_task_id=override,
    )
    return decide_schedule(queued).target_task_id


def _recent_events(store, *, limit: int = 8) -> tuple[WorkbenchRecentEvent, ...]:
    with store._read_tx():
        rows = store._conn.execute(
            """
            SELECT 'event' AS stream, seq AS stream_seq, event_id AS entry_id,
                   kind, occurred_at AS recorded_at, task_id, episode_id,
                   outcome, NULL AS reason
              FROM events
            UNION ALL
            SELECT 'decision' AS stream, seq AS stream_seq,
                   decision_id AS entry_id, kind, decided_at AS recorded_at,
                   task_id, episode_id, NULL AS outcome, reason
              FROM decisions
             ORDER BY recorded_at DESC, stream_seq DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(
        WorkbenchRecentEvent(
            stream=row["stream"],
            stream_seq=int(row["stream_seq"]),
            entry_id=row["entry_id"],
            kind=row["kind"],
            recorded_at=row["recorded_at"],
            task_id=row["task_id"],
            episode_id=row["episode_id"],
            outcome=row["outcome"],
            reason=row["reason"],
        )
        for row in rows
    )


def _task_view(store, snapshot, task, hub) -> WorkbenchTask:
    episode = _current_episode(snapshot, task.task_id)
    runtime_binding, session = _session_facts(store, episode, hub)
    current_judgment, next_steps = _snapshot_facts(store, episode)
    return WorkbenchTask(
        task_id=task.task_id,
        goal=task.original_goal,
        constraints=task.appended_constraints,
        status=task.status.value,
        priority=task.priority,
        warm=task.warm,
        warm_rank=task.warm_rank,
        is_foreground=snapshot.foreground_task_id == task.task_id,
        waiting=_waiting_payload(task.waiting_condition),
        pending_request=_pending_request_payload(
            hub,
            runtime_binding,
            task.waiting_condition,
        ),
        episode_id=episode.episode_id if episode is not None else None,
        episode_status=episode.status.value if episode is not None else None,
        agent_session_id=(
            runtime_binding.agent_session_id if runtime_binding is not None else None
        ),
        runtime=runtime_binding.runtime if runtime_binding is not None else None,
        model=session.model if session is not None else None,
        effort=session.effort if session is not None else None,
        connected=session.connected if session is not None else None,
        running=session.running if session is not None else None,
        can_send_message=session is not None and not runtime_binding.possible_orphan,
        current_judgment=current_judgment,
        next_steps=next_steps,
        updated_at=task.updated_at,
    )


def _candidate_payloads(
    default_repository,
    incubation_repository,
) -> tuple[dict[str, Any], ...]:
    candidates: list[dict[str, Any]] = []
    if default_repository is not None:
        for candidate in default_repository.pending_candidates():
            candidates.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "source_kind": "default",
                    "task_id": None,
                    "title": candidate.content,
                    "related_question": candidate.related_question,
                    "source_refs": candidate.source_refs,
                    "why_useful": candidate.why_useful,
                    "new_points": (),
                    "verification": candidate.verification,
                    "uncertainty": candidate.uncertainty,
                    "status": candidate.status.value,
                    "runtime": candidate.runtime,
                    "effective_model": candidate.effective_model,
                    "tier": candidate.tier,
                    "created_at": candidate.created_at,
                }
            )
    if incubation_repository is not None:
        for plan, candidate in incubation_repository.pending_review_candidates():
            candidates.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "source_kind": "incubation",
                    "task_id": plan.task_id,
                    "title": candidate.proposal,
                    "related_question": plan.unresolved_question,
                    "source_refs": candidate.source_refs,
                    "why_useful": None,
                    "new_points": candidate.new_points,
                    "verification": candidate.verification,
                    "uncertainty": candidate.uncertainty,
                    "status": candidate.status.value,
                    "runtime": candidate.runtime,
                    "effective_model": candidate.effective_model,
                    "tier": candidate.tier,
                    "created_at": candidate.created_at,
                }
            )
    candidates.sort(
        key=lambda candidate: (
            candidate["created_at"],
            candidate["candidate_id"],
        ),
        reverse=True,
    )
    return tuple(candidates)


def build_workbench_state(
    store,
    *,
    hub=None,
    pending_override_task_id: str | None = None,
    default_repository=None,
    incubation_repository=None,
) -> WorkbenchState:
    snapshot = store.read_snapshot()
    next_task_id = _next_task_id(
        store,
        snapshot.foreground_task_id,
        pending_override_task_id,
    )
    tasks = [
        _task_view(store, snapshot, task, hub)
        for task in snapshot.tasks
        if not task.status.is_terminal
    ]
    tasks.sort(
        key=lambda task: (
            0
            if task.is_foreground
            else 1
            if task.task_id == next_task_id
            else 2
            if task.warm
            else 3,
            task.warm_rank if task.warm_rank is not None else 10**9,
            -task.priority,
            task.updated_at,
            task.task_id,
        )
    )
    return WorkbenchState(
        as_of=store.journal_boundary(),
        automation_paused=automation_is_paused(store),
        foreground_task_id=snapshot.foreground_task_id,
        next_task_id=next_task_id,
        tasks=tuple(tasks),
        candidates=_candidate_payloads(
            default_repository,
            incubation_repository,
        ),
        recent_events=_recent_events(store),
    )
