"""把 CC workflow 磁盘快照转换为 wire tree，不执行 I/O。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from trowel_py.cc_host.schemas import (
    WorkflowAgentInfo,
    WorkflowPhaseInfo,
    WorkflowTreeEvent,
)

WireState = Literal["queued", "running", "done", "failed"]
WireStatus = Literal["running", "completed", "killed", "failed"]


def agent_state_from_cc(
    cc_state: Any,
    *,
    state_map: Mapping[str, str],
) -> WireState:
    if isinstance(cc_state, str):
        mapped = state_map.get(cc_state)
        if mapped is not None:
            return mapped  # type: ignore[return-value]
    return "running"


def status_from_cc(cc_status: Any) -> WireStatus:
    if cc_status in ("running", "completed", "killed", "failed"):
        return cc_status  # type: ignore[return-value]
    return "running"


def args_to_str(
    raw: Any,
    *,
    dumps: Callable[..., str] = json.dumps,
) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    try:
        return dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(raw)


def int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def phase_from_top(
    phase: Any,
    *,
    phase_type: Callable[..., WorkflowPhaseInfo],
    to_optional_str: Callable[[Any], str | None],
) -> WorkflowPhaseInfo | None:
    if not isinstance(phase, dict):
        return None
    title = to_optional_str(phase.get("title"))
    if not title:
        return None
    return phase_type(
        title=title,
        detail=to_optional_str(phase.get("detail")),
    )


def phases_from_progress(
    events: list[Any],
    *,
    phase_type: Callable[..., WorkflowPhaseInfo],
    to_optional_int: Callable[[Any], int | None],
    to_optional_str: Callable[[Any], str | None],
) -> list[WorkflowPhaseInfo]:
    phase_events = [
        event
        for event in events
        if isinstance(event, dict) and event.get("type") == "workflow_phase"
    ]
    phase_events.sort(key=lambda event: to_optional_int(event.get("index")) or 0)
    phases: list[WorkflowPhaseInfo] = []
    for event in phase_events:
        title = to_optional_str(event.get("title"))
        if title:
            phases.append(phase_type(title=title, detail=None))
    return phases


def agent_from_event(
    event: Any,
    *,
    agent_type: Callable[..., WorkflowAgentInfo],
    state_from_cc: Callable[[Any], WireState],
    to_optional_int: Callable[[Any], int | None],
    to_optional_str: Callable[[Any], str | None],
) -> WorkflowAgentInfo | None:
    if not isinstance(event, dict):
        return None
    agent_id = to_optional_str(event.get("agentId"))
    label = to_optional_str(event.get("label"))
    if not agent_id or not label:
        return None
    return agent_type(
        agent_id=agent_id,
        label=label,
        phase_index=to_optional_int(event.get("phaseIndex")),
        phase_title=to_optional_str(event.get("phaseTitle")),
        model=to_optional_str(event.get("model")),
        state=state_from_cc(event.get("state")),
        tokens=to_optional_int(event.get("tokens")),
        tool_calls=to_optional_int(event.get("toolCalls")),
        last_tool_name=to_optional_str(event.get("lastToolName")),
        duration_ms=to_optional_int(event.get("durationMs")),
        prompt_preview=to_optional_str(event.get("promptPreview")),
        result_preview=to_optional_str(event.get("resultPreview")),
    )


def parse_workflow_tree(
    workflow: dict[str, Any],
    *,
    event_type: Callable[..., WorkflowTreeEvent],
    phase_from_top_entry: Callable[[Any], WorkflowPhaseInfo | None],
    phases_from_events: Callable[[list[Any]], list[WorkflowPhaseInfo]],
    agent_from_progress: Callable[[Any], WorkflowAgentInfo | None],
    normalize_status: Callable[[Any], WireStatus],
    stringify_args: Callable[[Any], str | None],
    to_optional_int: Callable[[Any], int | None],
    to_optional_str: Callable[[Any], str | None],
) -> WorkflowTreeEvent:
    progress = workflow.get("workflowProgress")
    events: list[Any] = list(progress) if isinstance(progress, list) else []

    top_phases = workflow.get("phases")
    phases: list[WorkflowPhaseInfo] = []
    if isinstance(top_phases, list) and top_phases:
        for item in top_phases:
            phase = phase_from_top_entry(item)
            if phase is not None:
                phases.append(phase)
    else:
        phases = phases_from_events(events)

    agents: list[WorkflowAgentInfo] = []
    for item in events:
        if isinstance(item, dict) and item.get("type") == "workflow_agent":
            agent = agent_from_progress(item)
            if agent is not None:
                agents.append(agent)

    return event_type(
        type="workflow_tree",
        run_id=str(workflow.get("runId", "")),
        task_id=to_optional_str(workflow.get("taskId")),
        name=str(workflow.get("workflowName", "")),
        args=stringify_args(workflow.get("args")),
        status=normalize_status(workflow.get("status")),
        agent_count=to_optional_int(workflow.get("agentCount")) or 0,
        done_count=sum(1 for agent in agents if agent.state == "done"),
        total_tokens=to_optional_int(workflow.get("totalTokens")),
        total_tool_calls=to_optional_int(workflow.get("totalToolCalls")),
        duration_ms=to_optional_int(workflow.get("durationMs")),
        phases=phases,
        agents=agents,
        error=to_optional_str(workflow.get("error")),
    )
