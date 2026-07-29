"""读取 Claude Code 会话 JSONL，并重建可供 Trowel 展示的历史事件。

回放会过滤 Claude Code 持久化的内部输入，并把磁盘上的 Workflow 快照插入对应
的工具调用之后。
"""

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
    """判断 Claude Code 会话 ID 能否直接用作日志文件名。

    Args:
        cc_session_id: 要检查的 Claude Code 会话 ID。

    Returns:
        ID 非空、不等于 ``.`` 或 ``..`` 且不含正反斜杠时为 True，否则为 False。
    """

    if not cc_session_id or cc_session_id in (".", ".."):
        return False
    if "/" in cc_session_id or "\\" in cc_session_id:
        return False
    return True


def _parse_iso_ts(ts: Any) -> datetime | None:
    """解析 Claude Code 历史记录中的 ISO 时间戳。

    Args:
        ts: 历史记录提供的原始时间值。

    Returns:
        解析后的时间；输入不是非空字符串或格式无效时为 None。末尾的 ``Z`` 按
        UTC 处理。
    """

    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ts_delta_seconds(prev_ts: Any, cur_ts: Any) -> int | None:
    """计算两个历史时间戳之间取整后的正秒数。

    Args:
        prev_ts: 时间区间起点的原始 ISO 时间值。
        cur_ts: 时间区间终点的原始 ISO 时间值。

    Returns:
        通过 ``round()`` 得到的正秒数，最小为 1；任一时间无效或取整结果不为正
        时为 None。
    """

    start = _parse_iso_ts(prev_ts)
    end = _parse_iso_ts(cur_ts)
    if start is None or end is None:
        return None
    delta = round((end - start).total_seconds())
    if delta <= 0:
        return None
    return max(1, delta)


def _compute_thinking_duration(prev_ts: Any, thinking_ts: Any) -> int | None:
    """用前一条历史记录与 thinking 记录的时间差估算思考时长。

    历史记录没有 heartbeat，因此该结果只是相邻时间戳的近似值。

    Args:
        prev_ts: 最近一条带时间戳的历史记录时间。
        thinking_ts: 当前 thinking 记录的时间。

    Returns:
        估算的正秒数；任一时间无效或时间差不为正时为 None。
    """

    return _ts_delta_seconds(prev_ts, thinking_ts)


def _close_pending_turn(
    events: list[TrowelEvent], pending: dict[str, Any] | None
) -> None:
    """用本轮最后一条记录的时间更新对应用户消息耗时。

    Args:
        events: 已翻译的历史事件；函数会替换待结算的 UserEvent。
        pending: 待结算用户消息的索引、起始时间和最后时间；没有待结算消息时为
            None。
    """

    if pending is None:
        return
    duration = _ts_delta_seconds(pending["user_ts"], pending["last_ts"])
    index = pending["user_idx"]
    events[index] = events[index].model_copy(update={"duration_seconds": duration})


def parse_history(workdir: str, cc_session_id: str) -> list[TrowelEvent]:
    """按 JSONL 记录顺序重建指定 Claude Code 会话的历史事件。

    无法解析的 JSON 行会被跳过。Workflow 快照按 ``startTime`` 和文件名排序，
    依次插入 Workflow 工具调用之后；没有对应调用的剩余快照追加到末尾。

    Args:
        workdir: 会话运行时的工作目录，用于定位 Claude Code 项目日志目录。
        cc_session_id: 要回放的 Claude Code 会话 ID。

    Returns:
        重建后的 Trowel 事件；会话 ID 不可用或日志文件不存在时为空列表。

    Raises:
        OSError: 日志文件存在但无法打开或读取。
    """

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
    """读取并排列会话目录中可用的 Workflow 快照。

    无法读取、无法解析或内容不是对象的快照会被跳过。

    Args:
        transcript_dir: Claude Code 保存该会话 Workflow 数据的目录。

    Returns:
        按 ``startTime``、文件名升序排列的 Workflow 树事件。
    """

    workflow_dir = transcript_dir / "workflows"
    if not workflow_dir.is_dir():
        return []
    snapshots: list[tuple[int, str, WorkflowTreeEvent]] = []
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
        snapshots.append((start, path.name, event))
    snapshots.sort(key=lambda item: (item[0], item[1]))
    return [event for _, _, event in snapshots]


def _translate_line(ev: dict[str, Any], prev_ts: str | None) -> list[TrowelEvent]:
    """把一条已解析的 JSONL 记录翻译为展示事件。

    Args:
        ev: 一条 Claude Code 历史记录。
        prev_ts: 此前最近一条带时间戳记录的 ISO 时间；没有时为 None。

    Returns:
        由该记录生成的事件；不支持的记录类型或非成功 result 生成空列表。
    """

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
    """恢复用户输入的 slash command，并过滤 Claude Code 内部注入。

    Args:
        text: Claude Code 写入历史记录的 user 文本。

    Returns:
        可展示的用户原文或还原后的 slash command；内部注入内容为空字符串。
    """

    return _run_clean_user_text(
        text,
        command_name_re=_COMMAND_NAME_RE,
        command_args_re=_COMMAND_ARGS_RE,
        skill_trigger_re=_SKILL_TRIGGER_RE,
    )


def _translate_user(ev: dict[str, Any]) -> list[TrowelEvent]:
    """将一条历史 user 记录翻译为用户消息或工具结果。

    Args:
        ev: Claude Code 历史中的 user 记录。

    Returns:
        可展示的用户消息或工具结果；内部注入、meta 记录和不支持的内容生成空
        列表。
    """

    return _run_translate_user(
        ev,
        clean_user_text=_clean_user_text,
        write_diff_from_result=write_diff_from_cc_result,
        user_event_type=UserEvent,
        tool_result_event_type=ToolResultEvent,
    )


def _translate_assistant(ev: dict[str, Any], prev_ts: str | None) -> list[TrowelEvent]:
    """将一条历史 assistant 记录拆成文本、思考和工具事件。

    Args:
        ev: Claude Code 历史中的 assistant 记录。
        prev_ts: 此前最近一条带时间戳记录的 ISO 时间；没有时为 None。

    Returns:
        按内容块顺序生成的展示事件；消息内容不是列表时为空列表。
    """

    return _run_translate_assistant(
        ev,
        prev_ts,
        compute_thinking_duration=_compute_thinking_duration,
        text_event_type=TextEvent,
        thinking_event_type=ThinkingEvent,
        elicitation_event_type=ElicitationRequestEvent,
        tool_call_event_type=ToolCallEvent,
    )
