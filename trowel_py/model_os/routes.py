"""Model OS yield 的本地 HTTP 薄边界。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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
