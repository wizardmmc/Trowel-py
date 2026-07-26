"""Model OS yield 的本地 HTTP 薄边界。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from trowel_py.model_os.episode_starting import StartEpisodeCommand
from trowel_py.model_os.routing import (
    RouteMarker,
    RouteReviewClass,
    UserRoutePreference,
    build_route_gate,
    record_route_approval,
    record_route_review,
)
from trowel_py.model_os.store import TaskCommandError
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

router = APIRouter()


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


def _outcome_payload(outcome) -> dict[str, Any]:
    return {
        "decision_id": outcome.recorded.decision_id,
        "action": outcome.decision.action.value,
        "target_task_id": outcome.decision.target_task_id,
        "result_code": outcome.result_code,
    }


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
    if scheduler is not None:
        for event in events:
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
