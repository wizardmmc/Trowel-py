"""监视 CC 的 workflow 文件、journal 与 agent transcript。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from trowel_py.cc_host.workflow_journal import JsonlCursor
from trowel_py.cc_host.workflow_tree import (
    agent_from_event as _run_agent_from_event,
)
from trowel_py.cc_host.workflow_tree import (
    agent_state_from_cc as _run_agent_state_from_cc,
)
from trowel_py.cc_host.workflow_tree import args_to_str as _run_args_to_str
from trowel_py.cc_host.workflow_tree import int_or_none as _run_int_or_none
from trowel_py.cc_host.workflow_tree import (
    parse_workflow_tree as _run_parse_workflow_tree,
)
from trowel_py.cc_host.workflow_tree import (
    phase_from_top as _run_phase_from_top,
)
from trowel_py.cc_host.workflow_tree import (
    phases_from_progress as _run_phases_from_progress,
)
from trowel_py.cc_host.workflow_tree import status_from_cc as _run_status_from_cc
from trowel_py.cc_host.workflow_tree import str_or_none as _run_str_or_none
from trowel_py.cc_host.schemas import (
    WorkflowAgentInfo,
    WorkflowPhaseInfo,
    WorkflowTreeEvent,
)

logger = logging.getLogger(__name__)


def _wf_debug(msg: str) -> None:
    pass


# CC 用 ``start``/``progress`` 表示进行中，未知状态也必须保持可见而不能误报完成。
_AGENT_STATE_MAP: dict[str, str] = {
    "done": "done",
    "start": "running",
    "progress": "running",
    "error": "failed",
    "queued": "queued",
    "running": "running",
    "failed": "failed",
}

WireState = Literal["queued", "running", "done", "failed"]
WireStatus = Literal["running", "completed", "killed", "failed"]


def _agent_state_from_cc(cc_state: Any) -> WireState:
    return _run_agent_state_from_cc(cc_state, state_map=_AGENT_STATE_MAP)


def _status_from_cc(cc_status: Any) -> WireStatus:
    return _run_status_from_cc(cc_status)


def _args_to_str(raw: Any) -> str | None:
    return _run_args_to_str(raw, dumps=json.dumps)


def _int_or_none(value: Any) -> int | None:
    return _run_int_or_none(value)


def _str_or_none(value: Any) -> str | None:
    return _run_str_or_none(value)


def _phase_from_top(p: Any) -> WorkflowPhaseInfo | None:
    return _run_phase_from_top(
        p,
        phase_type=WorkflowPhaseInfo,
        to_optional_str=_str_or_none,
    )


def _phases_from_progress(events: list[Any]) -> list[WorkflowPhaseInfo]:
    return _run_phases_from_progress(
        events,
        phase_type=WorkflowPhaseInfo,
        to_optional_int=_int_or_none,
        to_optional_str=_str_or_none,
    )


def _agent_from_event(e: Any) -> WorkflowAgentInfo | None:
    return _run_agent_from_event(
        e,
        agent_type=WorkflowAgentInfo,
        state_from_cc=_agent_state_from_cc,
        to_optional_int=_int_or_none,
        to_optional_str=_str_or_none,
    )


def parse_workflow_tree(wf: dict[str, Any]) -> WorkflowTreeEvent:
    return _run_parse_workflow_tree(
        wf,
        event_type=WorkflowTreeEvent,
        phase_from_top_entry=_phase_from_top,
        phases_from_events=_phases_from_progress,
        agent_from_progress=_agent_from_event,
        normalize_status=_status_from_cc,
        stringify_args=_args_to_str,
        to_optional_int=_int_or_none,
        to_optional_str=_str_or_none,
    )


class WorkflowWatcher:
    """按会话轮询 CC 的 workflow 快照、journal 与 agent transcript。"""

    _TERMINAL_STATUSES = frozenset({"completed", "killed", "failed"})

    def __init__(self, transcript_dir: Path | None) -> None:
        self._dir = transcript_dir
        # 只在本 turn 出现 Workflow tool_use 后轮询，避免普通会话持续扫描磁盘。
        self._enabled = False
        # mtime 只在快照读取成功后提交，失败时必须允许下次重试同一文件。
        self._last_mtime: dict[str, float | None] = {}
        self._finished: set[str] = set()
        # enable 前已有的 run 归 history replay 所有，live watcher 不得重复发送。
        self._pre_existing: set[str] = set()
        # turn 终态只由 enable 后发现的 run 决定，不能被历史完成快照提前触发。
        self._tracked: set[str] = set()
        # wf.json 完成前，agent 的 live 状态来自对应 journal.jsonl。
        self._journal_cursors: dict[str, JsonlCursor] = {}
        self._journal_agents: dict[str, dict[str, WorkflowAgentInfo]] = {}

    def set_transcript_dir(self, transcript_dir: Path) -> None:
        self._dir = transcript_dir

    def enable(self) -> None:
        """启用轮询，并登记由 history replay 负责的既有 workflow。"""
        if self._enabled:
            return
        self._enabled = True
        if self._dir is not None:
            wf_dir = self._dir / "workflows"
            if wf_dir.is_dir():
                for f in wf_dir.glob("wf_*.json"):
                    self._pre_existing.add(f.stem)

    def resync(self) -> None:
        """在新 send 前重读非终态 run，接回跨 turn 完成的 workflow。"""
        self._last_mtime.clear()

    def _clear_run_cache(self, run_id: str) -> None:
        self._last_mtime.pop(run_id, None)
        self._tracked.discard(run_id)
        self._journal_cursors.pop(run_id, None)
        self._journal_agents.pop(run_id, None)

    def close(self) -> None:
        run_ids = (
            set(self._last_mtime)
            | self._tracked
            | set(self._journal_cursors)
            | set(self._journal_agents)
        )
        for run_id in run_ids:
            self._clear_run_cache(run_id)
        self._finished.clear()
        self._pre_existing.clear()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_watching(self) -> bool:
        if self._dir is None:
            return False
        return len(self._last_mtime) > len(self._finished)

    @property
    def all_done(self) -> bool:
        """当前 turn 发现的 workflow 是否都已终止。"""
        if not self._enabled:
            return True
        if not self._tracked:
            return False
        return all(rid in self._finished for rid in self._tracked)

    def poll(self) -> list[WorkflowTreeEvent]:
        """合并 wf.json 完成快照与 journal live 状态，按 runId 输出变更。"""
        if not self._enabled or self._dir is None:
            return []
        wf_dir = self._dir / "workflows"
        journal_root = self._dir / "subagents" / "workflows"
        run_ids: set[str] = set()
        if wf_dir.is_dir():
            for f in wf_dir.glob("wf_*.json"):
                run_ids.add(f.stem)
        if journal_root.is_dir():
            for d in journal_root.iterdir():
                if d.is_dir() and (d / "journal.jsonl").is_file():
                    run_ids.add(d.name)
        run_ids |= set(self._last_mtime)
        run_ids |= set(self._journal_agents)
        run_ids -= self._finished
        run_ids -= self._pre_existing
        if not run_ids:
            return []

        out: list[WorkflowTreeEvent] = []
        for run_id in sorted(run_ids):
            wf_path = wf_dir / f"{run_id}.json"
            journal_path = journal_root / run_id / "journal.jsonl"
            if wf_path.is_file():
                try:
                    mtime = wf_path.stat().st_mtime
                except OSError:
                    if not journal_path.is_file():
                        self._clear_run_cache(run_id)
                    continue
                if self._last_mtime.get(run_id) == mtime:
                    continue
                snapshot = self._read_snapshot(run_id, wf_path)
                if snapshot is None:
                    continue
                self._last_mtime[run_id] = mtime
                self._tracked.add(run_id)
                out.append(snapshot)
                if snapshot.status in self._TERMINAL_STATUSES:
                    self._finished.add(run_id)
            else:
                if not journal_path.is_file():
                    self._clear_run_cache(run_id)
                    continue
                snapshot = self._read_journal_snapshot(run_id, journal_root)
                if snapshot is not None:
                    self._tracked.add(run_id)
                    out.append(snapshot)
        if out:
            _wf_debug(
                f"watcher.poll pushed {len(out)}: {[(s.run_id, s.status) for s in out]}"
            )
        return out

    def _read_journal_snapshot(
        self, run_id: str, journal_root: Path
    ) -> WorkflowTreeEvent | None:
        """从 journal 的增量事件构造可被最终 wf.json 替换的运行中快照。"""
        journal_path = journal_root / run_id / "journal.jsonl"
        if not journal_path.is_file():
            return None
        cursor = self._journal_cursors.setdefault(run_id, JsonlCursor())
        agents = self._journal_agents.setdefault(run_id, {})
        try:
            for raw in cursor.read(journal_path):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                agent_id = ev.get("agentId")
                if not isinstance(agent_id, str) or not agent_id:
                    continue
                etype = ev.get("type")
                if etype == "started":
                    label = self._agent_label_from_transcript(
                        journal_root, run_id, agent_id
                    )
                    agents[agent_id] = WorkflowAgentInfo(
                        agent_id=agent_id,
                        label=label or agent_id,
                        state="running",
                    )
                elif etype == "result":
                    prev = agents.get(agent_id)
                    agents[agent_id] = (
                        prev.model_copy(update={"state": "done"})
                        if prev is not None
                        else WorkflowAgentInfo(
                            agent_id=agent_id, label=agent_id, state="done"
                        )
                    )
        except OSError as exc:
            logger.debug("journal tail failed (%s): %s", run_id, exc)
            return None
        if not agents:
            return None
        # transcript 可能晚于 started 落盘；先回退到 agentId，后续轮询再补 label。
        for agent_id, agent in list(agents.items()):
            if agent.label == agent_id:
                label = self._agent_label_from_transcript(
                    journal_root, run_id, agent_id
                )
                if label:
                    agents[agent_id] = agent.model_copy(update={"label": label})
        done = sum(1 for a in agents.values() if a.state == "done")
        return WorkflowTreeEvent(
            type="workflow_tree",
            run_id=run_id,
            name=run_id,
            status="running",
            agent_count=len(agents),
            done_count=done,
            agents=list(agents.values()),
        )

    def _agent_label_from_transcript(
        self, journal_root: Path, run_id: str, agent_id: str
    ) -> str | None:
        """用 agent transcript 首条 prompt 补足 journal 缺失的 live label。"""
        path = journal_root / run_id / f"agent-{agent_id}.jsonl"
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                first = fh.readline()
            d = json.loads(first)
        except (OSError, json.JSONDecodeError):
            return None
        msg = d.get("message") if isinstance(d, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            prompt = content
        elif isinstance(content, list):
            prompt = next(
                (
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ),
                "",
            )
        else:
            return None
        prompt = prompt.strip()
        if not prompt:
            return None
        return prompt[:40] + ("…" if len(prompt) > 40 else "")

    def _read_snapshot(self, run_id: str, path: Path) -> WorkflowTreeEvent | None:
        """读取完整快照；文件未写完或解析失败时保留到下次轮询重试。"""
        try:
            raw = path.read_text(encoding="utf-8")
            wf = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("workflow snapshot unreadable (%s): %s", run_id, exc)
            return None
        if not isinstance(wf, dict):
            return None
        try:
            return parse_workflow_tree(wf)
        except Exception as exc:  # noqa: BLE001
            logger.warning("workflow snapshot parse failed (%s): %s", run_id, exc)
            return None
