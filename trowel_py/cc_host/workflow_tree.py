"""把 CC workflow 快照转换为前端树事件，不读写磁盘。"""

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
    """使用调用方提供的映射表归一化 CC Agent 状态。

    映射值直接作为结果返回，由调用方保证它属于 `WireState`。未知状态和非字符串
    值都按 `running` 处理，避免把仍需展示的 Agent 误报为终态。

    Args:
        cc_state: `workflow_agent.state` 的原始值。
        state_map: CC 状态名到前端状态名的映射。

    Returns:
        映射后的 Agent 状态；无法映射时为 `running`。
    """

    if isinstance(cc_state, str):
        mapped = state_map.get(cc_state)
        if mapped is not None:
            return mapped  # type: ignore[return-value]
    return "running"


def status_from_cc(cc_status: Any) -> WireStatus:
    """将 workflow 状态限制为前端支持的四个值。

    Args:
        cc_status: workflow 顶层 `status` 的原始值。

    Returns:
        `running`、`completed`、`killed` 或 `failed`；其他值按 `running`
        处理。
    """

    if cc_status == "completed":
        return "completed"
    if cc_status == "killed":
        return "killed"
    if cc_status == "failed":
        return "failed"
    return "running"


def args_to_str(
    raw: Any,
    *,
    dumps: Callable[..., str] = json.dumps,
) -> str | None:
    """将 workflow 参数转为可展示文本。

    `None` 和字符串分别原样返回；其他值优先编码为 JSON，并通过
    `ensure_ascii=False` 保留非 ASCII 字符。编码抛出 `TypeError` 或
    `ValueError` 时改用 `str()`。

    Args:
        raw: workflow 顶层 `args` 的原始值。
        dumps: JSON 编码函数；调用时传入 `ensure_ascii=False`。

    Returns:
        可展示文本；`raw` 为 `None` 时返回 `None`。
    """

    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    try:
        return dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(raw)


def int_or_none(value: Any) -> int | None:
    """将 workflow 中可缺省的计数和时长转换为整数。

    `None` 和布尔值返回 `None`，整数原样返回，有限浮点数由 `int()` 向零截断。
    其他值先转成字符串再解析；转换触发 `TypeError` 或 `ValueError` 时返回
    `None`。

    Args:
        value: workflow 中计数或时长字段的原始值。

    Returns:
        转换后的整数，或 `None`。

    Raises:
        ValueError: 浮点数是 `NaN`。
        OverflowError: 浮点数是正无穷或负无穷。
    """

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
    """将 workflow 的可选字段转换为字符串。

    Args:
        value: workflow 字段的原始值。

    Returns:
        原字符串或 `str(value)` 的结果；`value` 为 `None` 时返回 `None`。
    """

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
    """从 workflow 顶层阶段条目构造阶段信息。

    非字典条目以及转换后没有标题的条目会被丢弃。

    Args:
        phase: 顶层 `phases` 列表中的一个条目。
        phase_type: 构造阶段信息的函数。
        to_optional_str: 归一化标题和说明的函数。

    Returns:
        包含标题和说明的阶段信息；条目不可用时返回 `None`。
    """

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
    """从 `workflow_phase` 进度事件中按 index 恢复阶段列表。

    缺失或无法转换的 index 按 0 排序，相同 index 保持原顺序。没有标题的事件
    被丢弃；进度事件恢复的阶段不带 detail。

    Args:
        events: workflow 顶层 `workflowProgress` 中的条目。
        phase_type: 构造阶段信息的函数。
        to_optional_int: 归一化阶段 index 的函数。
        to_optional_str: 归一化阶段标题的函数。

    Returns:
        按 index 排列的阶段信息。
    """

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
    """将一个 `workflow_agent` 条目转换为 Agent 信息。

    非字典条目会被丢弃；`agentId` 或 `label` 转换后为空的条目也会被丢弃。

    Args:
        event: `workflowProgress` 中的 Agent 条目。
        agent_type: 构造 Agent 信息的函数。
        state_from_cc: 归一化 Agent 状态的函数。
        to_optional_int: 归一化计数、时长和阶段 index 的函数。
        to_optional_str: 归一化 Agent 文本字段的函数。

    Returns:
        转换后的 Agent 信息；条目不可用时返回 `None`。
    """

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
    """将一份 CC workflow 快照转换为完整的前端树事件。

    顶层 `phases` 是非空列表时只转换该列表，即使其中没有可用阶段也不再回退；
    否则从 `workflowProgress` 的 `workflow_phase` 条目恢复阶段。Agent 列表只转换
    `workflowProgress` 中的 `workflow_agent` 条目；`done_count` 统计转换后状态为
    `done` 的 Agent。

    Args:
        workflow: 已解析为字典的 CC workflow 快照。
        event_type: 构造树事件的函数。
        phase_from_top_entry: 转换顶层阶段条目的函数。
        phases_from_events: 从进度事件恢复阶段列表的函数。
        agent_from_progress: 转换 Agent 进度条目的函数。
        normalize_status: 归一化 workflow 状态的函数。
        stringify_args: 将 workflow 参数转换为展示文本的函数。
        to_optional_int: 归一化可选整数的函数。
        to_optional_str: 归一化可选字符串的函数。

    Returns:
        包含 workflow 元数据、阶段和 Agent 的树事件；实时监视与历史回放使用
        同一结果结构。
    """

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
