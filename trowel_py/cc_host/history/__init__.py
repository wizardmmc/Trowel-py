"""将 CC 会话 JSONL 回放为统一事件。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.cc_host.history.messages import (
    clean_user_text as _run_clean_user_text,
)
from trowel_py.cc_host.history.messages import (
    translate_assistant as _run_translate_assistant,
)
from trowel_py.cc_host.history.messages import (
    translate_user as _run_translate_user,
)
from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug
from trowel_py.cc_host.tool_use_result import write_diff_from_cc_result
from trowel_py.cc_host.workflow_watcher import parse_workflow_tree
from trowel_py.cc_host.schemas import (
    ElicitationRequestEvent,
    FinishedEvent,
    SessionStartedEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    TrowelEvent,
    UserEvent,
    WorkflowTreeEvent,
)

logger = logging.getLogger(__name__)


def _is_safe_session_id(cc_session_id: str) -> bool:
    """只接受不能越出会话目录的文件名。"""
    if not cc_session_id or cc_session_id in (".", ".."):
        return False
    if "/" in cc_session_id or "\\" in cc_session_id:
        return False
    return True


def _parse_iso_ts(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ts_delta_seconds(prev_ts: Any, cur_ts: Any) -> int | None:
    """返回两个有效时间戳间至少一秒的正时长。"""
    start = _parse_iso_ts(prev_ts)
    end = _parse_iso_ts(cur_ts)
    if start is None or end is None:
        return None
    delta = round((end - start).total_seconds())
    if delta <= 0:
        return None
    return max(1, delta)


def _compute_thinking_duration(prev_ts: Any, thinking_ts: Any) -> int | None:
    """历史记录没有 heartbeat，只能用相邻条目的时间差近似。"""
    return _ts_delta_seconds(prev_ts, thinking_ts)


def _close_pending_turn(
    events: list[TrowelEvent], pending: dict[str, Any] | None
) -> None:
    if pending is None:
        return
    duration = _ts_delta_seconds(pending["user_ts"], pending["last_ts"])
    index = pending["user_idx"]
    events[index] = events[index].model_copy(update={"duration_seconds": duration})


def parse_history(workdir: str, cc_session_id: str) -> list[TrowelEvent]:
    """按时间顺序回放会话；文件不存在时返回空列表。"""
    slug = workdir_to_slug(workdir)
    if not _is_safe_session_id(cc_session_id):
        return []
    path = cc_projects_root() / slug / f"{cc_session_id}.jsonl"
    if not path.is_file():
        return []

    events: list[TrowelEvent] = []
    prev_ts: str | None = None
    pending: dict[str, Any] | None = None
    workflow_snapshots = _load_workflow_snapshots(path.parent / cc_session_id)
    workflows_injected = 0

    with path.open("r", encoding="utf-8", errors="replace") as file:
        for raw in file:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("skipping unparseable line in %s", path)
                continue

            cur_ts = event.get("timestamp") if isinstance(event, dict) else None
            for translated in _translate_line(event, prev_ts):
                if isinstance(translated, UserEvent):
                    _close_pending_turn(events, pending)
                    pending = {
                        "user_idx": len(events),
                        "user_ts": cur_ts,
                        "last_ts": cur_ts,
                    }
                events.append(translated)
                if (
                    workflows_injected < len(workflow_snapshots)
                    and isinstance(translated, ToolCallEvent)
                    and translated.tool_name == "Workflow"
                ):
                    events.append(workflow_snapshots[workflows_injected])
                    workflows_injected += 1

            if isinstance(cur_ts, str) and cur_ts:
                prev_ts = cur_ts
                if pending is not None:
                    pending = {**pending, "last_ts": cur_ts}

    events.extend(workflow_snapshots[workflows_injected:])
    _close_pending_turn(events, pending)
    return events


def _load_workflow_snapshots(
    transcript_dir: Path,
) -> list[WorkflowTreeEvent]:
    """读取并按开始时间排列已持久化的 Workflow 完成态。"""
    workflow_dir = transcript_dir / "workflows"
    if not workflow_dir.is_dir():
        return []
    snapshots: list[tuple[int, WorkflowTreeEvent]] = []
    for path in workflow_dir.glob("wf_*.json"):
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("skipping unreadable workflow snapshot %s", path)
            continue
        if not isinstance(workflow, dict):
            continue
        try:
            event = parse_workflow_tree(workflow)
        except Exception as exc:  # noqa: BLE001 — 坏快照不能中断整个回放
            logger.warning(
                "workflow snapshot parse failed (%s): %s",
                path,
                exc,
            )
            continue
        start_raw = workflow.get("startTime")
        start = (
            int(start_raw)
            if isinstance(start_raw, (int, float)) and not isinstance(start_raw, bool)
            else 0
        )
        snapshots.append((start, event))
    snapshots.sort(key=lambda item: item[0])
    return [event for _, event in snapshots]


def _translate_line(ev: dict[str, Any], prev_ts: str | None) -> list[TrowelEvent]:
    """把一条 JSONL 记录分派为零到多个事件。"""
    top = ev.get("type")
    if top == "system" and ev.get("subtype") == "init":
        return [
            SessionStartedEvent(
                model=str(ev.get("model", "")),
                cwd=str(ev.get("cwd", "")),
                cc_session_id=str(ev.get("session_id", "")),
                tools=list(ev.get("tools", [])),
                slash_commands=list(ev.get("slash_commands", [])),
                skills=list(ev.get("skills", [])),
                agents=list(ev.get("agents", [])),
            )
        ]
    if top == "user":
        return _translate_user(ev)
    if top == "assistant":
        return _translate_assistant(ev, prev_ts)
    if top == "result" and ev.get("subtype") == "success":
        return [
            FinishedEvent(
                usage=dict(ev.get("usage", {}) or {}),
                total_cost_usd=float(ev.get("total_cost_usd", 0.0) or 0.0),
                num_turns=int(ev.get("num_turns", 0) or 0),
            )
        ]
    return []


_COMMAND_NAME_RE = re.compile(r"<command-name>\s*/?\s*(\S+?)\s*</command-name>")
_COMMAND_ARGS_RE = re.compile(
    r"<command-args>(.*?)</command-args>",
    re.DOTALL,
)
_SKILL_TRIGGER_RE = re.compile(
    r"^Use the Skill tool with skill='([^']+)'\.\s*(.*)$",
    re.DOTALL,
)


def _clean_user_text(text: str) -> str:
    return _run_clean_user_text(
        text,
        command_name_re=_COMMAND_NAME_RE,
        command_args_re=_COMMAND_ARGS_RE,
        skill_trigger_re=_SKILL_TRIGGER_RE,
    )


def _translate_user(ev: dict[str, Any]) -> list[TrowelEvent]:
    return _run_translate_user(
        ev,
        clean_user_text=_clean_user_text,
        write_diff_from_result=write_diff_from_cc_result,
        user_event_type=UserEvent,
        tool_result_event_type=ToolResultEvent,
    )


def _translate_assistant(ev: dict[str, Any], prev_ts: str | None) -> list[TrowelEvent]:
    return _run_translate_assistant(
        ev,
        prev_ts,
        compute_thinking_duration=_compute_thinking_duration,
        text_event_type=TextEvent,
        thinking_event_type=ThinkingEvent,
        elicitation_event_type=ElicitationRequestEvent,
        tool_call_event_type=ToolCallEvent,
    )
