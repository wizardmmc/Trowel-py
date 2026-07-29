"""监视 CC 的 workflow 文件、journal 与 Agent transcript。

本模块还保留 workflow 树解析函数的兼容入口；具体解析由 `workflow_tree` 实现。
"""

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
    """接收但不输出 workflow 调试消息，保留现有诊断调用点。"""

    pass


# CC 用 `start`/`progress` 表示进行中；未知状态也必须保持可见，不能误报完成。
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
    """将 CC Agent 状态转换为前端状态，未知值按 `running` 处理。"""

    return _run_agent_state_from_cc(cc_state, state_map=_AGENT_STATE_MAP)


def _status_from_cc(cc_status: Any) -> WireStatus:
    """将 CC workflow 状态转换为前端状态，未知值按 `running` 处理。"""

    return _run_status_from_cc(cc_status)


def _args_to_str(raw: Any) -> str | None:
    """将 workflow 参数转为可展示文本。"""

    return _run_args_to_str(raw, dumps=json.dumps)


def _int_or_none(value: Any) -> int | None:
    """将 workflow 的阶段 index、计数和毫秒时长转换为整数。

    `None` 和布尔值返回 `None`，整数原样返回，有限浮点数向零截断。其他非浮点值
    先转成字符串再解析，转换触发 `TypeError` 或 `ValueError` 时返回 `None`。

    Raises:
        ValueError: 浮点数是 `NaN`。
        OverflowError: 浮点数是正无穷或负无穷。
    """

    return _run_int_or_none(value)


def _str_or_none(value: Any) -> str | None:
    """将 workflow 的非 `None` 字段转换为字符串。"""

    return _run_str_or_none(value)


def _phase_from_top(p: Any) -> WorkflowPhaseInfo | None:
    """将顶层阶段条目转为阶段信息。"""

    return _run_phase_from_top(
        p,
        phase_type=WorkflowPhaseInfo,
        to_optional_str=_str_or_none,
    )


def _phases_from_progress(events: list[Any]) -> list[WorkflowPhaseInfo]:
    """从进度事件中恢复阶段列表。"""

    return _run_phases_from_progress(
        events,
        phase_type=WorkflowPhaseInfo,
        to_optional_int=_int_or_none,
        to_optional_str=_str_or_none,
    )


def _agent_from_event(e: Any) -> WorkflowAgentInfo | None:
    """将进度事件转为 Agent 信息。"""

    return _run_agent_from_event(
        e,
        agent_type=WorkflowAgentInfo,
        state_from_cc=_agent_state_from_cc,
        to_optional_int=_int_or_none,
        to_optional_str=_str_or_none,
    )


def parse_workflow_tree(wf: dict[str, Any]) -> WorkflowTreeEvent:
    """把已解析的 CC workflow 快照转换为前端树事件。"""

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
    """按会话轮询 CC 的 workflow 快照、journal 与 Agent transcript。

    观察器在 service 看到 Workflow 工具调用后才启用。启用时已经存在的完整快照
    由历史回放发布，不进入实时轮询；之后从完整快照或 journal 发现的 run 才加入
    `all_done` 判断。journal 提供完整快照出现前的 Agent 状态，
    `wf_<runId>.json` 出现后替换该临时快照。

    Attributes:
        enabled: 是否已经启用文件轮询。
        is_watching: 完整快照 mtime 缓存的诊断状态，不统计只有 journal 的 run。
        all_done: 启用后发现的 run 是否都已发布终态完整快照。
    """

    _TERMINAL_STATUSES = frozenset({"completed", "killed", "failed"})

    def __init__(self, transcript_dir: Path | None) -> None:
        """创建尚未启用的 workflow 文件观察器。

        Args:
            transcript_dir: 当前 CC 会话的 transcript 目录；原生会话 ID 尚未确定时
                为 `None`，之后由 `set_transcript_dir()` 补充。
        """

        self._dir = transcript_dir
        # 只有观察到 Workflow tool_use 后才启用，避免从未使用 Workflow 的会话持续扫描。
        self._enabled = False
        # mtime 只在快照读取成功后提交，失败时必须允许下次重试同一文件。
        self._last_mtime: dict[str, float | None] = {}
        self._finished: set[str] = set()
        # enable 时已有完整快照的 run 归历史回放所有，实时观察器不得重复发送。
        self._pre_existing: set[str] = set()
        # all_done 只统计 enable 后发现的 run，不能被历史完成快照提前触发。
        self._tracked: set[str] = set()
        # `wf_<runId>.json` 出现前，Agent 实时状态来自对应的 `journal.jsonl`。
        self._journal_cursors: dict[str, JsonlCursor] = {}
        self._journal_agents: dict[str, dict[str, WorkflowAgentInfo]] = {}

    def set_transcript_dir(self, transcript_dir: Path) -> None:
        """设置当前 CC 会话的 transcript 目录。

        Args:
            transcript_dir: 包含 `workflows/` 和 `subagents/workflows/` 的会话目录。
        """

        self._dir = transcript_dir

    def enable(self) -> None:
        """启用轮询，并登记由历史回放负责的既有完整快照。

        只有第一次调用生效；当时已绑定 transcript 目录时，将其中的
        `workflows/wf_*.json` 记为既有 run，避免实时路径重复发布。
        """
        if self._enabled:
            return
        self._enabled = True
        if self._dir is not None:
            wf_dir = self._dir / "workflows"
            if wf_dir.is_dir():
                for f in wf_dir.glob("wf_*.json"):
                    self._pre_existing.add(f.stem)

    def resync(self) -> None:
        """清空完整快照的 mtime 缓存，使下次轮询重读未终止 run 的完整快照。

        终态记录、run 跟踪集合和 journal 缓存保持不变，因此跨多次 `send()` 的
        workflow 仍沿用原有观察状态。
        """
        self._last_mtime.clear()

    def _clear_run_cache(self, run_id: str) -> None:
        """清除一个 run 的 mtime、跟踪状态和 journal 缓存。

        此方法不修改 `_finished` 和 `_pre_existing`。

        Args:
            run_id: 要停止跟踪的 workflow run ID。
        """

        self._last_mtime.pop(run_id, None)
        self._tracked.discard(run_id)
        self._journal_cursors.pop(run_id, None)
        self._journal_agents.pop(run_id, None)

    def close(self) -> None:
        """清空全部 run 缓存和集合，保留启用标记与 transcript 目录。"""

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
        """返回观察器是否已经启用。"""

        return self._enabled

    @property
    def is_watching(self) -> bool:
        """返回完整快照 mtime 数量是否多于已记录的终态 run 数量。

        未绑定 transcript 目录时返回 `False`。该值只用于诊断，不统计只有 journal
        的运行中 run，也不参与 `send()` 的终态判断。
        """

        if self._dir is None:
            return False
        return len(self._last_mtime) > len(self._finished)

    @property
    def all_done(self) -> bool:
        """判断启用后发现的 workflow 是否都已发布终态完整快照。

        观察器未启用时返回 `True`；已启用但尚未跟踪到 run 时返回 `False`。启用时
        已有完整快照的 run 不会加入跟踪集合。
        """
        if not self._enabled:
            return True
        if not self._tracked:
            return False
        return all(rid in self._finished for rid in self._tracked)

    def poll(self) -> list[WorkflowTreeEvent]:
        """轮询完整快照和 journal，并按 run ID 返回树事件。

        run ID 来自 `workflows/wf_*.json`、含 `journal.jsonl` 的 run 目录，以及已有
        mtime 和 Agent 缓存；已发布终态或启用时已有完整快照的 run 会被排除。
        完整快照只在 mtime 尚未成功处理时读取，读取或转换失败不提交 mtime，下次
        仍会重试。没有完整快照时，只要已经累积 Agent，每轮都会重发 journal 合并
        快照，以便补全迟到的 label。终态完整快照发布后不再轮询该 run。

        Returns:
            按 run ID 排序的树事件；观察器未启用、目录未知或没有可发布快照时
            返回空列表。
        """
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
        """从 journal 增量事件构造可被完整快照替换的运行中树事件。

        只处理带非空字符串 `agentId` 的 `started` 和 `result`。`started` 将 Agent
        记为运行中，`result` 将已有 Agent 标为完成，或补建一个已完成 Agent。空行、
        JSON 语法错误、非对象和其他事件类型会被忽略；journal 读取失败返回 `None`。

        Args:
            run_id: 当前 workflow run ID。
            journal_root: 包含各 run journal 和 Agent transcript 的目录。

        Returns:
            累积至今的运行中树事件；journal 不存在、读取失败或尚无有效 Agent 时
            返回 `None`。

        Raises:
            UnicodeDecodeError: journal 行或 Agent transcript 首行不是有效 UTF-8。
            AttributeError: transcript 第一个 `text` 块的 `text` 值不是字符串。
        """
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
        # Agent transcript 可能晚于 started 落盘；先用 agentId，后续轮询再补 label。
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
        """从 Agent transcript 首行提取运行中 label。

        content 为字符串时直接使用；为列表时取第一个 `text` 块。文本去除首尾空白，
        正文超过 40 个字符时截断为 40 个字符并追加省略号。文件不存在、读取失败、
        JSON 语法错误、content 既不是字符串也不是列表、列表中没有 `text` 块或文本
        为空时返回 `None`。

        Args:
            journal_root: 包含各 run journal 和 Agent transcript 的目录。
            run_id: 当前 workflow run ID。
            agent_id: 要补充 label 的 Agent ID。

        Returns:
            从 transcript 首行消息正文得到的短 label，或 `None`。

        Raises:
            UnicodeDecodeError: transcript 首行不是有效 UTF-8。
            AttributeError: 第一个 `text` 块的 `text` 值不是字符串。
        """
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
        """读取并转换一个完整 workflow 快照。

        `read_text()` 抛出 `OSError`、JSON 语法错误、顶层不是对象或树转换抛出普通
        异常时返回 `None`。调用方不会提交本次 mtime，并会在下次轮询重试。UTF-8
        解码失败则向上传播。

        Args:
            run_id: 用于诊断日志的 workflow run ID。
            path: `wf_<runId>.json` 的路径。

        Returns:
            转换后的树事件；本次无法取得有效快照时返回 `None`。

        Raises:
            UnicodeDecodeError: 快照文件不是有效 UTF-8。
        """
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
