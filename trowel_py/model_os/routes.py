"""Model OS yield 的本地 HTTP 薄边界。"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from trowel_py.model_os.episode_starting import StartEpisodeCommand
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


@router.post("/episodes/start")
async def start_episode(body: StartEpisodeBody, request: Request) -> StreamingResponse:
    """以同一 command 启动首段或 snapshot 后的 fresh Episode。"""

    starter = getattr(request.app.state, "model_os_episode_starter", None)
    if starter is None:
        raise HTTPException(status_code=503, detail="Model OS Episode runner unavailable")
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
    )

    async def stream():
        async for event in starter.start(command):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(stream(), media_type="text/event-stream")


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
                suggested_task_state=YieldSuggestedState(
                    body.suggested_task_state
                ),
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
