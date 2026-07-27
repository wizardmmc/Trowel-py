"""Model OS journal、explain 与 policy replay 的只读 HTTP 边界。"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from trowel_py.model_os.explain import DecisionNotFound
from trowel_py.model_os.journal import (
    InvalidJournalCursor,
    JournalBoundary,
    JournalFilter,
)
from trowel_py.model_os.observation.replay import replay_policy_decision

router = APIRouter()
_OBSERVATION_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")


def _boundary_payload(boundary: JournalBoundary) -> dict[str, int]:
    return {
        "event_seq": boundary.event_seq,
        "decision_seq": boundary.decision_seq,
    }


@router.get("/journal")
async def journal_page(
    request: Request,
    kinds: list[str] | None = Query(default=None),
    task_id: str | None = None,
    episode_id: str | None = None,
    correlation_id: str | None = None,
    limit: str = "100",
    cursor: str | None = None,
) -> dict[str, Any]:
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    structured = (*kinds,) if kinds is not None else ()
    structured += tuple(
        value
        for value in (task_id, episode_id, correlation_id)
        if value is not None
    )
    if (
        kinds is not None
        and len(kinds) > 64
        or any(_OBSERVATION_TOKEN.fullmatch(value) is None for value in structured)
    ):
        raise HTTPException(status_code=422, detail="invalid journal filter")
    if not limit.isdecimal() or not 1 <= int(limit) <= 500:
        raise HTTPException(status_code=422, detail="invalid journal limit")
    try:
        page = store.read_journal_page(
            journal_filter=JournalFilter(
                kinds=tuple(kinds) if kinds is not None else None,
                task_id=task_id,
                episode_id=episode_id,
                correlation_id=correlation_id,
            ),
            limit=int(limit),
            cursor=cursor,
        )
    except InvalidJournalCursor as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {
            "items": [asdict(item) for item in page.items],
            "as_of": _boundary_payload(page.as_of),
            "next_cursor": page.next_cursor,
        },
        "error": None,
    }


def _requested_boundary(
    *,
    event_seq: str | None,
    decision_seq: str | None,
) -> JournalBoundary | None:
    if (event_seq is None) != (decision_seq is None):
        raise HTTPException(
            status_code=422,
            detail="event_seq and decision_seq must be provided together",
        )
    if event_seq is None:
        return None
    assert decision_seq is not None
    if (
        len(event_seq) > 20
        or len(decision_seq) > 20
        or not event_seq.isdecimal()
        or not decision_seq.isdecimal()
    ):
        raise HTTPException(status_code=422, detail="invalid journal boundary")
    return JournalBoundary(event_seq=int(event_seq), decision_seq=int(decision_seq))


def _validate_subject_id(subject_id: str) -> None:
    if _OBSERVATION_TOKEN.fullmatch(subject_id) is None:
        raise HTTPException(status_code=422, detail="invalid explanation subject")


def _parse_window(value: str) -> datetime:
    if not value or len(value) > 64:
        raise HTTPException(status_code=422, detail="invalid metrics window")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid metrics window") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail="invalid metrics window")
    return parsed


@router.get("/decisions/{decision_id}/explain")
async def explain_decision(
    decision_id: str,
    request: Request,
    event_seq: str | None = None,
    decision_seq: str | None = None,
) -> dict[str, Any]:
    _validate_subject_id(decision_id)
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    boundary = _requested_boundary(
        event_seq=event_seq,
        decision_seq=decision_seq,
    ) or store.journal_boundary()
    try:
        explanation = store.explain_decision(decision_id, boundary=boundary)
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {
            **asdict(explanation),
            "as_of": _boundary_payload(boundary),
        },
        "error": None,
    }


async def _explain_scope(
    subject_kind: str,
    subject_id: str,
    request: Request,
    *,
    event_seq: str | None,
    decision_seq: str | None,
) -> dict[str, Any]:
    _validate_subject_id(subject_id)
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    boundary = _requested_boundary(
        event_seq=event_seq,
        decision_seq=decision_seq,
    ) or store.journal_boundary()
    explanation = store.explain_scope(
        subject_kind,
        subject_id,
        boundary=boundary,
    )
    return {"success": True, "data": asdict(explanation), "error": None}


@router.get("/tasks/{task_id}/explain")
async def explain_task(
    task_id: str,
    request: Request,
    event_seq: str | None = None,
    decision_seq: str | None = None,
) -> dict[str, Any]:
    return await _explain_scope(
        "task",
        task_id,
        request,
        event_seq=event_seq,
        decision_seq=decision_seq,
    )


@router.get("/episodes/{episode_id}/explain")
async def explain_episode(
    episode_id: str,
    request: Request,
    event_seq: str | None = None,
    decision_seq: str | None = None,
) -> dict[str, Any]:
    return await _explain_scope(
        "episode",
        episode_id,
        request,
        event_seq=event_seq,
        decision_seq=decision_seq,
    )


@router.get("/metrics")
async def metrics(
    request: Request,
    window_start: str,
    window_end: str,
    event_seq: str | None = None,
    decision_seq: str | None = None,
) -> dict[str, Any]:
    start = _parse_window(window_start)
    end = _parse_window(window_end)
    if start >= end:
        raise HTTPException(status_code=422, detail="invalid metrics window")
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    boundary = _requested_boundary(
        event_seq=event_seq,
        decision_seq=decision_seq,
    ) or store.journal_boundary()
    report = store.read_metrics(
        window_start=window_start,
        window_end=window_end,
        boundary=boundary,
    )
    return {"success": True, "data": asdict(report), "error": None}


@router.get("/decisions/{decision_id}/replay")
async def replay_decision(
    decision_id: str,
    request: Request,
    policy_version: str | None = None,
    event_seq: str | None = None,
    decision_seq: str | None = None,
) -> dict[str, Any]:
    _validate_subject_id(decision_id)
    store = getattr(request.app.state, "model_os_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Model OS store unavailable")
    if policy_version is not None and (
        len(policy_version) > 64
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", policy_version) is None
    ):
        raise HTTPException(status_code=422, detail="invalid policy version")
    boundary = _requested_boundary(
        event_seq=event_seq,
        decision_seq=decision_seq,
    ) or store.journal_boundary()
    try:
        report = replay_policy_decision(
            store,
            decision_id,
            policy_version=policy_version,
            boundary=boundary,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    data = asdict(report)
    data["status"] = report.status.value
    data["as_of"] = _boundary_payload(boundary)
    if report.input_boundary is not None:
        data["input_boundary"] = _boundary_payload(report.input_boundary)
    return {"success": True, "data": data, "error": None}
