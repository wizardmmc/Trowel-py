"""Runtime 活动事件折叠与 terminal 可信度分类。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trowel_py.model_os.yielding.models import TurnState


def fold_activity(
    state: TurnState,
    event_type: str,
    payload: Mapping[str, Any],
    item_id: object,
) -> None:
    if event_type == "tool_call" and item_id:
        state.safe_interrupt_window = False
        state.unresolved_tools[str(item_id)] = str(payload.get("tool_name") or "tool")
    elif event_type == "tool_result" and item_id:
        state.unresolved_tools.pop(str(item_id), None)
        _mark_safe_if_idle(state)
    elif event_type in {"text", "thinking", "thinking_progress", "tool_progress"}:
        state.safe_interrupt_window = False
    elif event_type == "subagent_progress":
        task_id = payload.get("task_id")
        status = str(payload.get("status", ""))
        if task_id and status in {"started", "progress", "running"}:
            state.safe_interrupt_window = False
            state.unresolved_subagents.add(str(task_id))
        elif task_id:
            state.unresolved_subagents.discard(str(task_id))
            _mark_safe_if_idle(state)


def _mark_safe_if_idle(state: TurnState) -> None:
    if not state.unresolved_tools and not state.unresolved_subagents:
        state.safe_interrupt_window = True


def terminal_code(event_type: str, payload: Mapping[str, Any]) -> str:
    if event_type == "error":
        return f"error:{payload.get('subclass', 'unknown')}"
    return event_type


def terminal_requires_reconcile(state: TurnState) -> bool:
    terminal = state.terminal_type or ""
    if state.interrupt_attempted and not state.interrupt_sent:
        return True
    if terminal == "session_exited" or terminal == "error:host_error":
        return True
    if terminal == "interrupted" and not state.interrupt_sent:
        return True
    if terminal.startswith("error:"):
        return not (
            state.registration.runtime == "claude_code"
            and terminal == "error:error_during_execution"
            and state.interrupt_sent
        )
    return False
