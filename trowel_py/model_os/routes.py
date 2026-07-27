"""Model OS yield 的本地 HTTP 薄边界。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from trowel_py.agent_host.hub import SessionHubError
from trowel_py.agent_host.routes import stream_message_response
from trowel_py.model_os.episode_starting import StartEpisodeCommand
from trowel_py.model_os.observation.routes import router as observation_router
from trowel_py.model_os.default_work.models import (
    DefaultWorkError,
    RunDefaultPilotCommand,
)
from trowel_py.model_os.incubation import (
    CreateIncubationPlanCommand,
    IncubationError,
    IncubationWakeCondition,
)
from trowel_py.model_os.routing import (
    RouteMarker,
    RouteReviewClass,
    UserRoutePreference,
    build_route_gate,
    record_route_approval,
    record_route_review,
)
from trowel_py.model_os.store import TaskCommandError, WarmFull
from trowel_py.model_os.types import (
    MemoryEligibility,
    SessionPurpose,
    SnapshotRef,
)
from trowel_py.model_os.yielding import (
    YieldControlError,
    YieldProposal,
    YieldSuggestedState,
    YieldWaitingCondition,
)
from trowel_py.model_os.waking import WakeConditionKind, WakeObservation
from trowel_py.model_os.work_broker import BudgetDimensions
from trowel_py.model_os.workbench import build_workbench_state, set_automation_paused

router = APIRouter()
router.include_router(observation_router)


class YieldWaitingConditionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1)
    condition_kind: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    match_params: dict[str, Any] | None = None
    deadline: str | None = None


class YieldProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    suggested_task_state: Literal["ready", "waiting_event", "waiting_user", "done"]
    waiting_condition: YieldWaitingConditionBody | None = None
    current_judgment: str = Field(min_length=1)
    next_steps: tuple[str, ...] = Field(max_length=3)
    continue_same_task: bool


class StartSnapshotRefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    committed_event_id: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)


class StartEpisodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_item_id: str = Field(min_length=1)
    task_id: str | None = None
    previous_episode_id: str | None = None
    previous_snapshot_ref: StartSnapshotRefBody | None = None
    runtime: Literal["claude_code", "codex"]
    model: str | None = None
    effort: str | None = None
    memory_enabled: bool = Field(strict=True)
    profile_enabled: bool = Field(strict=True)
    workdir: str = Field(min_length=1)
    session_purpose: Literal[
        "foreground", "default", "incubation", "maintenance", "experiment"
    ]
    memory_eligibility: Literal["eligible", "ineligible", "adopted"]
    permission: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    schedule_decision_id: str | None = Field(default=None, min_length=1)
    route_preference: Literal["auto", "fast", "deep"] = "auto"
    route_mandatory_markers: tuple[Literal["high_impact_irreversible"], ...] = ()
    route_pre_route_markers: tuple[
        Literal["exact_constraint_search", "multi_scenario_contingency"], ...
    ] = ()
    route_evaluation_domain: Literal[
        "coding", "research", "life", "other", "unknown"
    ] = "unknown"
    route_input_fact_refs: tuple[str, ...] = ()


class RouteReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Literal[
        "correct", "missed_deep_need", "unjustified_deep", "unknown"
    ]
    trusted_verifier: bool = Field(strict=True)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reviewer_ref: str = Field(min_length=1)


class RouteApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_ref: str = Field(min_length=1)


class WakeObservationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    kind: Literal["user_input", "manual"]
    target_ref: str = Field(min_length=1)


class SetTaskPriorityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(strict=True)
    idempotency_key: str = Field(min_length=1)


class RequestForegroundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1)


class SetTaskWarmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warm: bool = Field(strict=True)


class SetAutomationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool = Field(strict=True)
    idempotency_key: str = Field(min_length=1)


class ReplyWaitingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(min_length=1)
    answers: dict[str, str] | None = None
    decision: str | None = None


class WorkbenchInstructionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class RunDefaultPilotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    runtime: Literal["claude_code", "codex"]
    source_refs: tuple[str, ...]


class CandidateOutcomeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    outcome: Literal["adopted", "dismissed", "invalid"]
    reason: str | None = None


class IncubationWakeConditionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "time", "user_input", "observed_state", "host_event", "manual"
    ] | None = None
    target_ref: str = ""
    match_params: dict[str, Any] = Field(default_factory=dict)
    due_at: str | None = None


class IncubationBudgetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: int | None = Field(default=None, strict=True)


class IncubationSnapshotRefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str = ""
    version: int | None = Field(default=None, strict=True)
    committed_event_id: str = ""
    payload_hash: str = ""


class CreateIncubationPlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    task_id: str = ""
    prepared_snapshot_ref: IncubationSnapshotRefBody | None = None
    unresolved_question: str = ""
    wake_condition: IncubationWakeConditionBody | None = None
    deadline: str | None = None
    budget: IncubationBudgetBody | None = None
    runtime: Literal["claude_code", "codex"]


class IncubationControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)


class EarlyIncubationWakeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)


class CleanupIncubationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    before: str = Field(min_length=1)


def _candidate_payload(candidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "generation_id": candidate.generation_id,
        "content": candidate.content,
        "source_refs": list(candidate.source_refs),
        "related_question": candidate.related_question,
        "why_useful": candidate.why_useful,
        "verification": candidate.verification,
        "uncertainty": candidate.uncertainty,
        "runtime": candidate.runtime,
        "effective_model": candidate.effective_model,
        "tier": candidate.tier,
        "policy_version": candidate.policy_version,
        "status": candidate.status.value,
        "created_at": candidate.created_at,
        "shown_at": candidate.shown_at,
        "outcome_at": candidate.outcome_at,
        "outcome_reason": candidate.outcome_reason,
        "expires_at": candidate.expires_at,
    }


def _incubation_plan_payload(plan, candidate=None) -> dict[str, Any]:
    ref = plan.prepared_snapshot_ref
    wake = plan.wake_condition
    return {
        "plan_id": plan.plan_id,
        "command_id": plan.command_id,
        "task_id": plan.task_id,
        "work_item_id": plan.work_item_id,
        "prepared_snapshot_ref": {
            "episode_id": ref.episode_id,
            "version": ref.version,
            "committed_event_id": ref.committed_event_id,
            "payload_hash": ref.payload_hash,
        },
        "unresolved_question": plan.unresolved_question,
        "wake_condition": {
            "kind": wake.kind.value,
            "target_ref": wake.target_ref,
            "match_params": wake.match_params,
            "due_at": wake.due_at,
        },
        "deadline": plan.deadline,
        "budget": {
            "calls": plan.budget.calls,
            "tokens": plan.budget.tokens,
            "cost": plan.budget.cost,
            "wall_seconds": plan.budget.wall_seconds,
        },
        "runtime": plan.runtime,
        "cycle": plan.cycle,
        "max_scheduled_cycles": plan.max_scheduled_cycles,
        "status": plan.status.value,
        "stop_reason": plan.stop_reason,
        "episode_id": plan.episode_id,
        "model_called": plan.model_called,
        "effective_model": plan.effective_model,
        "usage": (
            {
                "input_tokens": plan.usage.input_tokens,
                "output_tokens": plan.usage.output_tokens,
                "wall_seconds": plan.usage.wall_seconds,
                "cost": plan.usage.cost,
            }
            if plan.usage is not None
            else None
        ),
        "candidate": (
            _incubation_candidate_payload(candidate) if candidate is not None else None
        ),
        "policy_version": plan.policy_version,
        "reframe_policy": plan.reframe_policy,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def _incubation_candidate_payload(candidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "plan_id": candidate.plan_id,
        "cycle": candidate.cycle,
        "proposal": candidate.proposal,
        "source_refs": list(candidate.source_refs),
        "new_points": list(candidate.new_points),
        "verification": candidate.verification,
        "uncertainty": candidate.uncertainty,
        "runtime": candidate.runtime,
        "effective_model": candidate.effective_model,
        "tier": candidate.tier,
        "policy_version": candidate.policy_version,
        "status": candidate.status.value,
        "created_at": candidate.created_at,
        "shown_at": candidate.shown_at,
        "outcome_at": candidate.outcome_at,
        "outcome_reason": candidate.outcome_reason,
        "expires_at": candidate.expires_at,
        "cleaned_at": candidate.cleaned_at,
    }


def _default_error(code: str, message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": {"code": code, "message": message},
        },
    )


def _incubation_service(request: Request):
    service = getattr(request.app.state, "model_os_incubation", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Model OS incubation unavailable")
    return service


@router.post("/incubation/plans")
async def create_incubation_plan(
    body: CreateIncubationPlanBody, request: Request
) -> Any:
    service = _incubation_service(request)
    ref = body.prepared_snapshot_ref
    wake = body.wake_condition
    budget = body.budget
    if (
        ref is None
        or not ref.episode_id.strip()
        or ref.version is None
        or ref.version < 1
        or not ref.committed_event_id.strip()
        or not ref.payload_hash.strip()
    ):
        return _default_error("snapshot_missing", "snapshot_missing", status_code=409)
    if wake is None:
        return _default_error(
            "wake_condition_missing", "wake_condition_missing", status_code=409
        )
    if wake.kind is None:
        return _default_error(
            "wake_condition_missing", "wake_condition_missing", status_code=409
        )
    if budget is None or budget.calls != 1:
        return _default_error("budget_denied", "budget_denied", status_code=409)
    try:
        plan = service.create_plan(
            CreateIncubationPlanCommand(
                command_id=body.command_id,
                task_id=body.task_id,
                prepared_snapshot_ref=SnapshotRef(
                    ref.episode_id,
                    ref.version,
                    ref.committed_event_id,
                    ref.payload_hash,
                ),
                unresolved_question=body.unresolved_question,
                wake_condition=IncubationWakeCondition(
                    kind=WakeConditionKind(wake.kind),
                    target_ref=wake.target_ref,
                    match_params=wake.match_params,
                    due_at=wake.due_at,
                ),
                deadline=body.deadline,
                budget=BudgetDimensions(
                    calls=budget.calls,
                ),
                runtime=body.runtime,
                occurred_at=datetime.now(timezone.utc),
            )
        )
    except (IncubationError, ValueError) as exc:
        code = exc.code if isinstance(exc, IncubationError) else "plan_invalid"
        return _default_error(code, str(exc), status_code=409)
    return {"success": True, "data": _incubation_plan_payload(plan), "error": None}


@router.get("/incubation/plans/{plan_id}")
async def read_incubation_plan(plan_id: str, request: Request) -> Any:
    service = _incubation_service(request)
    try:
        plan = service.repository.get_plan(plan_id)
        candidate = service.repository.mark_candidate_shown(
            plan_id, occurred_at=datetime.now(timezone.utc)
        )
    except IncubationError as exc:
        return _default_error(exc.code, exc.detail, status_code=404)
    return {
        "success": True,
        "data": _incubation_plan_payload(plan, candidate),
        "error": None,
    }


@router.post("/incubation/plans/{plan_id}/cancel")
async def cancel_incubation_plan(
    plan_id: str, body: IncubationControlBody, request: Request
) -> Any:
    service = _incubation_service(request)
    try:
        plan = service.repository.cancel_plan(
            plan_id,
            command_id=body.command_id,
            occurred_at=datetime.now(timezone.utc),
        )
    except IncubationError as exc:
        return _default_error(exc.code, exc.detail, status_code=409)
    return {"success": True, "data": _incubation_plan_payload(plan), "error": None}


@router.post("/incubation/plans/{plan_id}/wake")
async def wake_incubation_plan(
    plan_id: str, body: EarlyIncubationWakeBody, request: Request
) -> Any:
    service = _incubation_service(request)
    controller = getattr(request.app.state, "model_os_wake_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Model OS wake unavailable")
    events = controller.observe(
        WakeObservation(
            observation_id=body.observation_id,
            kind=WakeConditionKind.MANUAL,
            target_ref=plan_id,
            observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            source="user",
            details={},
        )
    )
    matched = [
        event for event in events if event.condition_id == f"incubation:{plan_id}"
    ]
    if not matched:
        return _default_error("wake_not_consumed", "Plan is not wakeable", status_code=409)
    for event in matched:
        service.trigger(event)
    plan = service.repository.get_plan(plan_id)
    return {"success": True, "data": _incubation_plan_payload(plan), "error": None}


@router.post("/incubation/candidates/{candidate_id}/outcome")
async def record_incubation_candidate_outcome(
    candidate_id: str,
    body: CandidateOutcomeBody,
    request: Request,
) -> Any:
    service = _incubation_service(request)
    try:
        candidate = service.repository.record_outcome(
            command_id=body.command_id,
            candidate_id=candidate_id,
            outcome=body.outcome,
            reason=body.reason,
            occurred_at=datetime.now(timezone.utc),
        )
    except (IncubationError, ValueError) as exc:
        code = exc.code if isinstance(exc, IncubationError) else "outcome_invalid"
        return _default_error(code, str(exc), status_code=409)
    return {
        "success": True,
        "data": _incubation_candidate_payload(candidate),
        "error": None,
    }


@router.post("/incubation/cleanup")
async def cleanup_incubation_artifacts(
    body: CleanupIncubationBody, request: Request
) -> Any:
    service = _incubation_service(request)
    try:
        result = service.cleanup_artifacts(
            command_id=body.command_id,
            before=body.before,
            occurred_at=datetime.now(timezone.utc),
        )
    except (IncubationError, ValueError) as exc:
        code = exc.code if isinstance(exc, IncubationError) else "cleanup_invalid"
        return _default_error(code, str(exc), status_code=409)
    return {
        "success": True,
        "data": result,
        "error": None,
    }


@router.get("/incubation/gate")
async def incubation_gate(request: Request) -> Any:
    service = _incubation_service(request)
    report = service.repository.gate_report()
    return {
        "success": True,
        "data": {
            "status": report.status,
            "outcome_count": report.outcome_count,
            "adopted": report.adopted,
            "invalid": report.invalid,
            "dismissed": report.dismissed,
            "adoption_rate": report.adoption_rate,
            "invalid_rate": report.invalid_rate,
            "verification_success": report.verification_success,
            "verification_total": report.verification_total,
            "automatic_incubation": report.automatic_incubation,
        },
        "error": None,
    }


@router.post("/default/pilot")
async def run_default_pilot(body: RunDefaultPilotBody, request: Request) -> Any:
    service = getattr(request.app.state, "model_os_default_work", None)
    if service is None:
        return _default_error(
            "default_work_unavailable", "Default work unavailable", status_code=503
        )
    try:
        result = await service.run(
            RunDefaultPilotCommand(body.command_id, body.runtime, body.source_refs)
        )
    except DefaultWorkError as exc:
        return _default_error(exc.code, exc.detail, status_code=409)
    return {
        "success": True,
        "data": {
            "work_item_id": result.work_item_id,
            "episode_id": result.episode_id,
            "generation_id": result.generation_id,
            "candidate_ids": list(result.candidate_ids),
            "candidates": [_candidate_payload(item) for item in result.candidates],
        },
        "error": None,
    }


@router.post("/default/candidates/{candidate_id}/outcome")
async def record_default_candidate_outcome(
    candidate_id: str,
    body: CandidateOutcomeBody,
    request: Request,
) -> Any:
    service = getattr(request.app.state, "model_os_default_work", None)
    if service is None:
        return _default_error(
            "default_work_unavailable", "Default work unavailable", status_code=503
        )
    try:
        candidate = service.repository.record_outcome(
            command_id=body.command_id,
            candidate_id=candidate_id,
            outcome=body.outcome,
            reason=body.reason,
            occurred_at=datetime.now(timezone.utc),
        )
    except (DefaultWorkError, ValueError) as exc:
        code = exc.code if isinstance(exc, DefaultWorkError) else "outcome_invalid"
        return _default_error(code, str(exc), status_code=409)
    return {"success": True, "data": _candidate_payload(candidate), "error": None}


@router.get("/default/gate")
async def default_gate(request: Request) -> Any:
    service = getattr(request.app.state, "model_os_default_work", None)
    if service is None:
        return _default_error(
            "default_work_unavailable", "Default work unavailable", status_code=503
        )
    report = service.repository.gate_report()
    return {
        "success": True,
        "data": {
            "status": report.status,
            "outcome_count": report.outcome_count,
            "adopted": report.adopted,
            "invalid": report.invalid,
            "dismissed": report.dismissed,
            "adoption_rate": report.adoption_rate,
            "invalid_rate": report.invalid_rate,
            "total_input_tokens": report.total_input_tokens,
            "total_output_tokens": report.total_output_tokens,
            "known_cost": report.known_cost,
            "unknown_cost_generations": report.unknown_cost_generations,
            "automatic_default": report.automatic_default,
        },
        "error": None,
    }


@router.get("/default/generations/{generation_id}")
async def read_default_generation(generation_id: str, request: Request) -> Any:
    service = getattr(request.app.state, "model_os_default_work", None)
    if service is None:
        return _default_error(
            "default_work_unavailable", "Default work unavailable", status_code=503
        )
    try:
        generation = service.repository.generation_view(generation_id)
    except KeyError:
        return _default_error(
            "generation_missing", "Default generation not found", status_code=404
        )
    return {"success": True, "data": generation, "error": None}


def _outcome_payload(outcome) -> dict[str, Any]:
    return {
        "decision_id": outcome.recorded.decision_id,
        "action": outcome.decision.action.value,
        "target_task_id": outcome.decision.target_task_id,
        "result_code": outcome.result_code,
    }


def _workbench_payload(request: Request) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    hub = getattr(request.app.state, "agent_hub", None)
    scheduler = getattr(request.app.state, "model_os_attention_scheduler", None)
    default_service = getattr(request.app.state, "model_os_default_work", None)
    incubation_service = getattr(request.app.state, "model_os_incubation", None)
    state = build_workbench_state(
        store,
        hub=hub,
        pending_override_task_id=(
            scheduler.pending_override_task_id() if scheduler is not None else None
        ),
        default_repository=(
            default_service.repository if default_service is not None else None
        ),
        incubation_repository=(
            incubation_service.repository if incubation_service is not None else None
        ),
    )
    return asdict(state)


def _workbench_sse_frame(payload: dict[str, Any]) -> bytes:
    envelope = {"success": True, "data": payload, "error": None}
    return f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n".encode()


@router.get("/workbench")
async def read_workbench(request: Request) -> dict[str, Any]:
    return {"success": True, "data": _workbench_payload(request), "error": None}


@router.get("/workbench/events")
async def stream_workbench(request: Request) -> StreamingResponse:
    async def stream():
        previous: str | None = None
        while not await request.is_disconnected():
            payload = _workbench_payload(request)
            fingerprint = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint != previous:
                previous = fingerprint
                yield _workbench_sse_frame(payload)
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/automation")
async def set_automation(
    body: SetAutomationBody,
    request: Request,
) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    try:
        set_automation_paused(
            store,
            paused=body.paused,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": _workbench_payload(request), "error": None}


@router.post("/tasks/{task_id}/warm")
async def set_task_warm(
    task_id: str,
    body: SetTaskWarmBody,
    request: Request,
) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    try:
        if body.warm:
            store.promote_to_warm(task_id)
        else:
            store.demote_to_backlog(task_id)
    except (TaskCommandError, WarmFull) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": _workbench_payload(request), "error": None}


@router.post("/workbench/tasks/{task_id}/reply")
async def reply_waiting_task(
    task_id: str,
    body: ReplyWaitingBody,
    request: Request,
) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    hub = getattr(request.app.state, "agent_hub", None)
    wake = getattr(request.app.state, "model_os_wake_controller", None)
    scheduler = getattr(request.app.state, "model_os_attention_scheduler", None)
    if store is None or hub is None or wake is None:
        raise HTTPException(status_code=503, detail="Waiting reply unavailable")
    task = next(
        (
            candidate
            for candidate in store.read_snapshot().tasks
            if candidate.task_id == task_id
        ),
        None,
    )
    waiting = task.waiting_condition if task is not None else None
    if waiting is None or waiting.correlation_id != body.correlation_id:
        raise HTTPException(status_code=409, detail="Waiting request changed")
    episode_id = waiting.episode_id
    binding = (
        store.episode_runtime_binding(episode_id)
        if episode_id is not None
        else None
    )
    if binding is None:
        raise HTTPException(status_code=409, detail="Waiting session unavailable")
    try:
        pending = hub.pending_request(
            binding.agent_session_id,
            body.correlation_id,
        )
    except SessionHubError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if pending is None:
        raise HTTPException(status_code=409, detail="Waiting request is no longer active")
    if pending["kind"] == "input":
        if body.answers is None or body.decision is not None:
            raise HTTPException(status_code=422, detail="Input answers are required")
        expected = {
            question.get("question")
            for question in pending["questions"]
            if isinstance(question.get("question"), str)
        }
        if not expected or set(body.answers) != expected:
            raise HTTPException(status_code=409, detail="Answers do not match questions")
        payload = {"cancel": False, "answers": body.answers}
    else:
        if body.decision is None or body.answers is not None:
            raise HTTPException(status_code=422, detail="Approval decision is required")
        available = pending["available_decisions"]
        if body.decision not in available:
            raise HTTPException(status_code=409, detail="Decision is no longer available")
        payload = {
            "request_id": body.correlation_id,
            "decision": body.decision,
        }
    try:
        queued = wake.queue_for_session(
            binding.agent_session_id,
            correlation_id=body.correlation_id,
            runtime_generation=hub.runtime_generation(binding.agent_session_id),
            payload=payload,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if scheduler is not None:
        await scheduler.trigger(queued.wake_id)
    return {
        "success": True,
        "data": {
            "queued": True,
            "episode_id": queued.episode_id,
        },
        "error": None,
    }


@router.post("/workbench/instruction")
async def send_workbench_instruction(
    body: WorkbenchInstructionBody,
    request: Request,
) -> StreamingResponse:
    store = getattr(request.app.state, "model_os_store", None)
    hub = getattr(request.app.state, "agent_hub", None)
    if store is None or hub is None:
        raise HTTPException(status_code=503, detail="Workbench instruction unavailable")
    snapshot = store.read_snapshot()
    if snapshot.foreground_task_id != body.task_id:
        raise HTTPException(status_code=409, detail="Foreground task changed")
    episodes = [
        episode
        for episode in snapshot.episodes
        if episode.task_id == body.task_id and not episode.status.is_terminal
    ]
    episode = max(episodes, key=lambda item: item.updated_at) if episodes else None
    binding = (
        store.episode_runtime_binding(episode.episode_id)
        if episode is not None
        else None
    )
    if binding is None or binding.possible_orphan:
        raise HTTPException(status_code=409, detail="Foreground session unavailable")
    return stream_message_response(
        binding.agent_session_id,
        body.text,
        request=request,
        hub=hub,
    )


@router.post("/tasks/{task_id}/priority")
async def set_task_priority(
    task_id: str,
    body: SetTaskPriorityBody,
    request: Request,
) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    scheduler = getattr(request.app.state, "model_os_attention_scheduler", None)
    if store is None or scheduler is None:
        raise HTTPException(status_code=503, detail="Model OS scheduler unavailable")
    try:
        trigger = store.set_task_priority(
            task_id,
            priority=body.priority,
            idempotency_key=body.idempotency_key,
        )
    except TaskCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    outcome = await scheduler.trigger(trigger)
    return {"success": True, "data": _outcome_payload(outcome), "error": None}


@router.post("/tasks/{task_id}/foreground")
async def request_foreground(
    task_id: str,
    body: RequestForegroundBody,
    request: Request,
) -> dict[str, Any]:
    scheduler = getattr(request.app.state, "model_os_attention_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Model OS scheduler unavailable")
    try:
        outcome = await scheduler.request_foreground(
            task_id,
            idempotency_key=body.idempotency_key,
        )
    except TaskCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": _outcome_payload(outcome), "error": None}


@router.post("/episodes/start")
async def start_episode(body: StartEpisodeBody, request: Request) -> StreamingResponse:
    """以同一 command 启动首段或 snapshot 后的 fresh Episode。"""

    starter = getattr(request.app.state, "model_os_episode_starter", None)
    if starter is None:
        raise HTTPException(
            status_code=503, detail="Model OS Episode runner unavailable"
        )
    raw_ref = body.previous_snapshot_ref
    command = StartEpisodeCommand(
        work_item_id=body.work_item_id,
        task_id=body.task_id,
        previous_episode_id=body.previous_episode_id,
        previous_snapshot_ref=(
            SnapshotRef(**raw_ref.model_dump()) if raw_ref is not None else None
        ),
        runtime=body.runtime,
        model=body.model,
        effort=body.effort,
        memory_enabled=body.memory_enabled,
        profile_enabled=body.profile_enabled,
        workdir=body.workdir,
        session_purpose=SessionPurpose(body.session_purpose),
        memory_eligibility=MemoryEligibility(body.memory_eligibility),
        permission=body.permission,
        idempotency_key=body.idempotency_key,
        schedule_decision_id=body.schedule_decision_id,
        route_preference=UserRoutePreference(body.route_preference),
        route_mandatory_markers=tuple(
            RouteMarker(item) for item in body.route_mandatory_markers
        ),
        route_pre_route_markers=tuple(
            RouteMarker(item) for item in body.route_pre_route_markers
        ),
        route_evaluation_domain=body.route_evaluation_domain,
        route_input_fact_refs=body.route_input_fact_refs,
    )

    async def stream():
        async for event in starter.start(command):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/routing/gate")
async def route_gate(request: Request) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    gate = build_route_gate(store)
    return {
        "success": True,
        "data": {
            "live_episodes": gate.live_episodes,
            "reviewed_episodes": gate.reviewed_episodes,
            "domains": list(gate.domains),
            "trusted_verifier_episodes": gate.trusted_verifier_episodes,
            "trusted_verifier_versions": list(gate.trusted_verifier_versions),
            "missed_deep_need": gate.missed_deep_need,
            "unjustified_deep": gate.unjustified_deep,
            "review_unknown": gate.review_unknown,
            "user_override_total": gate.user_override_total,
            "user_override_executed": gate.user_override_executed,
            "actual_match_total": gate.actual_match_total,
            "actual_match_executed": gate.actual_match_executed,
            "ready_for_human_review": gate.ready_for_human_review,
            "canary_approved": gate.canary_approved,
            "as_of": {
                "event_seq": gate.as_of.event_seq,
                "decision_seq": gate.as_of.decision_seq,
            },
        },
        "error": None,
    }


@router.post("/routing/decisions/{decision_id}/review")
async def review_route(
    decision_id: str, body: RouteReviewBody, request: Request
) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    try:
        event_id = record_route_review(
            store,
            decision_id,
            classification=RouteReviewClass(body.classification),
            trusted_verifier=body.trusted_verifier,
            evidence_refs=body.evidence_refs,
            reviewer_ref=body.reviewer_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": {"event_id": event_id}, "error": None}


@router.post("/routing/approval")
async def approve_route(body: RouteApprovalBody, request: Request) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    try:
        event_id = record_route_approval(store, reviewer_ref=body.reviewer_ref)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": {"event_id": event_id}, "error": None}


@router.post("/wake")
async def submit_wake(body: WakeObservationBody, request: Request) -> dict[str, Any]:
    """接受用户或人工唤醒；机器 observation 只能来自进程内 observer。"""

    controller = getattr(request.app.state, "model_os_wake_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Model OS wake is unavailable")
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    events = controller.observe(
        WakeObservation(
            observation_id=body.observation_id,
            kind=WakeConditionKind(body.kind),
            target_ref=body.target_ref,
            observed_at=observed_at,
            source="user",
            details={},
        )
    )
    scheduler = getattr(request.app.state, "model_os_attention_scheduler", None)
    incubation = getattr(request.app.state, "model_os_incubation", None)
    for event in events:
        handled = incubation is not None and incubation.trigger(event)
        if not handled and scheduler is not None:
            await scheduler.trigger(event.wake_id)
    return {
        "success": True,
        "data": {
            "wakes": [
                {
                    "wake_id": event.wake_id,
                    "task_id": event.task_id,
                    "episode_id": event.episode_id,
                    "disposition": event.disposition.value,
                }
                for event in events
            ]
        },
        "error": None,
    }


@router.post("/sessions/{session_id}/yield")
async def propose_yield(
    session_id: str, body: YieldProposalBody, request: Request
) -> dict[str, Any]:
    hub = getattr(request.app.state, "agent_hub", None)
    coordinator = getattr(request.app.state, "model_os_yield_coordinator", None)
    if hub is None or coordinator is None:
        raise HTTPException(status_code=503, detail="Model OS yield is unavailable")
    binding = hub.get(session_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not binding.model_os_mcp_enabled:
        raise HTTPException(status_code=403, detail="session is not Model OS managed")
    waiting = body.waiting_condition
    try:
        receipt = await coordinator.propose(
            session_id,
            YieldProposal(
                reason=body.reason,
                suggested_task_state=YieldSuggestedState(body.suggested_task_state),
                waiting_condition=(
                    YieldWaitingCondition(
                        cause=waiting.cause,
                        condition_kind=waiting.condition_kind,
                        target_ref=waiting.target_ref,
                        match_params=waiting.match_params,
                        deadline=waiting.deadline,
                    )
                    if waiting is not None
                    else None
                ),
                current_judgment=body.current_judgment,
                next_steps=body.next_steps,
                continue_same_task=body.continue_same_task,
            ),
        )
    except (ValueError, YieldControlError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {
            "status": receipt.status,
            "episode_id": receipt.episode_id,
            "checkpoint_ref": receipt.checkpoint_ref,
        },
        "error": None,
    }
