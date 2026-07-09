"""Watch cc Workflow runs by reading the on-disk state cc maintains.

cc runs Workflows in the background and pushes NOTHING about them to its
``--stream-json`` stdout (verified by binary reverse + transcript scan — see
wiki/raw/2026-07-07-tcc-workflow-render-bug.md). cc's own TUI reads the
on-disk ``wf_<runId>.json`` (stat-polling it), and so does trowel: this module
is trowel's equivalent of cc's workflow-progress reducer.

Two layers, kept separate so the translation is unit-testable with no IO:

* :func:`parse_workflow_tree` — pure: ``wf_<runId>.json`` dict in ->
  :class:`WorkflowTreeEvent` out. The single source of truth for the tree;
  used by BOTH the live watcher and history replay (C-1 invariant).
* :class:`WorkflowWatcher` — the IO/lifecycle layer: stat-polls
  ``workflows/wf_*.json`` while a workflow runs and yields full snapshots on
  change (live path, slice-036 P1). P2 adds journal.jsonl tailing; P3 adds
  per-agent transcript tailing for tool transparency.

Data shapes are verified against a real deep-research run (runId
wf_a5daf5bf-47b) — see spikes + tests/cc_host/test_workflow_watcher.py.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from trowel_py.schemas.cc_host import (
    WorkflowAgentInfo,
    WorkflowPhaseInfo,
    WorkflowTreeEvent,
)

logger = logging.getLogger(__name__)

def _wf_debug(msg: str) -> None:
    """Diagnostic stub — see service._wf_debug."""
    pass

# cc's internal agent state → trowel wire state. cc writes ``start``/``progress``
# for in-flight agents (both render as the running pulse), ``error`` for a
# failed agent, and ``queued``/``done`` directly. Anything unrecognized falls
# back to ``running`` so a never-seen node stays visible (not silently done).
_AGENT_STATE_MAP: dict[str, str] = {
    "done": "done",
    "start": "running",
    "progress": "running",
    "error": "failed",
    "queued": "queued",
    "running": "running",  # defensive: some cc builds may already use ours
    "failed": "failed",
}

WireState = Literal["queued", "running", "done", "failed"]
WireStatus = Literal["running", "completed", "killed", "failed"]


def _agent_state_from_cc(cc_state: Any) -> WireState:
    """Map cc's internal agent state to the trowel wire enum.

    Args:
        cc_state: the raw ``state`` value from a workflow_agent entry
            (``"done"``/``"start"``/``"progress"``/``"error"``/``"queued"``).
            May be None or an unknown string on a newer/cc variant.

    Returns:
        The wire state (queued/running/done/failed). Unknown values map to
        ``running`` so an unfinished-looking node never silently disappears.
    """
    if isinstance(cc_state, str):
        mapped = _AGENT_STATE_MAP.get(cc_state)
        if mapped is not None:
            return mapped  # type: ignore[return-value]
    return "running"


def _status_from_cc(cc_status: Any) -> WireStatus:
    """Map cc's workflow status to the wire enum; unknown → running.

    Args:
        cc_status: the top-level ``status`` from wf_<runId>.json
            (running/completed/killed/failed).

    Returns:
        The wire status. An unrecognized value degrades to ``running`` so the
        watcher keeps polling (rather than dropping a still-running workflow).
    """
    if cc_status in ("running", "completed", "killed", "failed"):
        return cc_status  # type: ignore[return-value]
    return "running"


def _args_to_str(raw: Any) -> str | None:
    """Flatten the workflow ``args`` field to a display string.

    cc normally stores the user's question text here verbatim. If a future cc
    variant writes a structured object, stringify it rather than crash (the
    frontend renders args as truncated text in the workflow header).

    Args:
        raw: the raw ``args`` value (usually a str, sometimes None).

    Returns:
        The args as a string, or None when absent.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(raw)


def _int_or_none(value: Any) -> int | None:
    """Coerce a wf.json numeric field to int; None if absent / not a number.

    cc writes ints, but defend against a float / str / None on a half-written
    file (workflow still booting).
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


def _str_or_none(value: Any) -> str | None:
    """Return the value as a string if present, else None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _phase_from_top(p: Any) -> WorkflowPhaseInfo | None:
    """Build a WorkflowPhaseInfo from a top-level phases[] entry.

    The top-level array is the only place ``detail`` appears (workflowProgress's
    workflow_phase events carry only {type, index, title}). Returns None for a
    malformed entry so one bad phase can't abort the whole tree.
    """
    if not isinstance(p, dict):
        return None
    title = _str_or_none(p.get("title"))
    if not title:
        return None
    return WorkflowPhaseInfo(title=title, detail=_str_or_none(p.get("detail")))


def _phases_from_progress(events: list[Any]) -> list[WorkflowPhaseInfo]:
    """Rebuild phases from workflow_phase events (fallback when the top-level
    phases[] is absent). Ordered by the events' ``index`` field; detail is
    unavailable here (None)."""
    phase_events = [
        e
        for e in events
        if isinstance(e, dict) and e.get("type") == "workflow_phase"
    ]
    phase_events.sort(key=lambda e: _int_or_none(e.get("index")) or 0)
    out: list[WorkflowPhaseInfo] = []
    for e in phase_events:
        title = _str_or_none(e.get("title"))
        if title:
            out.append(WorkflowPhaseInfo(title=title, detail=None))
    return out


def _agent_from_event(e: Any) -> WorkflowAgentInfo | None:
    """Build a WorkflowAgentInfo from one workflow_agent progress event.

    Returns None for a malformed entry (no agentId / no label) so it is skipped
    without aborting the tree.
    """
    if not isinstance(e, dict):
        return None
    agent_id = _str_or_none(e.get("agentId"))
    label = _str_or_none(e.get("label"))
    if not agent_id or not label:
        return None
    return WorkflowAgentInfo(
        agent_id=agent_id,
        label=label,
        phase_index=_int_or_none(e.get("phaseIndex")),
        phase_title=_str_or_none(e.get("phaseTitle")),
        model=_str_or_none(e.get("model")),
        state=_agent_state_from_cc(e.get("state")),
        tokens=_int_or_none(e.get("tokens")),
        tool_calls=_int_or_none(e.get("toolCalls")),
        last_tool_name=_str_or_none(e.get("lastToolName")),
        duration_ms=_int_or_none(e.get("durationMs")),
        prompt_preview=_str_or_none(e.get("promptPreview")),
        result_preview=_str_or_none(e.get("resultPreview")),
    )


def parse_workflow_tree(wf: dict[str, Any]) -> WorkflowTreeEvent:
    """Translate one wf_<runId>.json dict into a WorkflowTreeEvent snapshot.

    Pure: no IO, no logging of contents. The live WorkflowWatcher and history
    replay both call this so the live and reloaded trees are isomorphic by
    construction (slice-036 invariant C-1).

    Args:
        wf: the parsed contents of ``workflows/wf_<runId>.json``. Top-level
            fields (runId/workflowName/status/agentCount/phases/
            workflowProgress/...) are verified against a real deep-research run.

    Returns:
        A full WorkflowTreeEvent snapshot. ``done_count`` is computed from the
        agents list (count of state=='done'); ``phases`` come from the top-level
        ``phases`` array (falling back to workflow_phase events); every
        optional field degrades to None on a half-written file.
    """
    progress = wf.get("workflowProgress")
    events: list[Any] = list(progress) if isinstance(progress, list) else []

    # phases: prefer the top-level array (it carries detail); fall back to the
    # workflow_phase progress events when the top-level array is absent/empty.
    top_phases = wf.get("phases")
    phases: list[WorkflowPhaseInfo] = []
    if isinstance(top_phases, list) and top_phases:
        for p in top_phases:
            phase = _phase_from_top(p)
            if phase is not None:
                phases.append(phase)
    else:
        phases = _phases_from_progress(events)

    agents: list[WorkflowAgentInfo] = []
    for e in events:
        if isinstance(e, dict) and e.get("type") == "workflow_agent":
            agent = _agent_from_event(e)
            if agent is not None:
                agents.append(agent)

    done_count = sum(1 for a in agents if a.state == "done")

    return WorkflowTreeEvent(
        type="workflow_tree",
        run_id=str(wf.get("runId", "")),
        task_id=_str_or_none(wf.get("taskId")),
        name=str(wf.get("workflowName", "")),
        args=_args_to_str(wf.get("args")),
        status=_status_from_cc(wf.get("status")),
        agent_count=_int_or_none(wf.get("agentCount")) or 0,
        done_count=done_count,
        total_tokens=_int_or_none(wf.get("totalTokens")),
        total_tool_calls=_int_or_none(wf.get("totalToolCalls")),
        duration_ms=_int_or_none(wf.get("durationMs")),
        phases=phases,
        agents=agents,
        error=_str_or_none(wf.get("error")),
    )


# ── WorkflowWatcher: live IO layer (slice-036 P1) ──────────────────────────
#
# stat-polls workflows/wf_<runId>.json under a session transcript dir and yields
# a full WorkflowTreeEvent whenever the file changed. Mounted on the
# service.send() main loop (1s readline heartbeat = the poll cadence). P2 will
# add journal.jsonl tailing for sub-second agent churn; P3 per-agent transcript
# tailing for tool transparency.


class WorkflowWatcher:
    """Stat-poll wf_<runId>.json files for live workflow progress (slice-036 P1).

    One watcher per CC session. ``start_dir`` records that a Workflow tool_use
    was seen so polling begins; ``poll()`` stat-checks every known runId's file
    and emits a snapshot only when mtime advanced. A workflow leaves polling
    once its status is terminal (completed/killed/failed) — one final snapshot
    is emitted on that transition.

    The watcher does NOT parse narrator text for taskId/runId (fragile — the
    narrator template varies and deep-research rewrites it). Instead it globs
    ``workflows/wf_*.json`` for runIds, the same way cc's own TUI discovers them.
    """

    _TERMINAL_STATUSES = frozenset({"completed", "killed", "failed"})

    def __init__(self, transcript_dir: Path | None) -> None:
        """Bind to a session transcript dir.

        Args:
            transcript_dir: ``<projects-root>/<slug>/<cc_session_id>`` — the dir
                whose ``workflows/`` subdir holds wf_<runId>.json. None when the
                session has no resumable transcript yet (fresh, pre-init); the
                watcher stays inert until ``set_transcript_dir`` is called.
        """
        self._dir = transcript_dir
        # Inert until the service sees a Workflow tool_use (``enable()``). Keeps
        # every readline tick from globbing workflows/ on sessions that never run
        # a workflow — the common case.
        self._enabled = False
        # runId -> last mtime seen (None = not yet emitted). Only tracks files
        # whose mtime advanced since the last snapshot, so an unchanged file
        # yields no event (cc stat-polls the same way).
        self._last_mtime: dict[str, float | None] = {}
        # runIds already emitted in a terminal state — skip further polling.
        self._finished: set[str] = set()
        # runIds that existed BEFORE enable() (historical workflows from a
        # resumed session). The live watcher must NOT re-emit these — history
        # replay owns them. Only workflows appearing after enable are pushed.
        self._pre_existing: set[str] = set()
        # runIds discovered AFTER enable (this turn's workflows). all_done
        # keys off these so a pre-existing completed wf.json doesn't make the
        # watcher falsely report "all done" before the new workflow starts.
        self._tracked: set[str] = set()
        # slice-036 P2: journal.jsonl tail state. cc writes wf.json only ONCE
        # the workflow finishes (verified: ctime == mtime == startTime+duration);
        # while it runs, live agent joins/state come from
        # subagents/workflows/wf_<runId>/journal.jsonl (started/result events).
        self._journal_offset: dict[str, int] = {}
        self._journal_agents: dict[str, dict[str, WorkflowAgentInfo]] = {}

    def set_transcript_dir(self, transcript_dir: Path) -> None:
        """Point the watcher at the session transcript dir once cc_session_id
        is known (learned mid-turn from system/init)."""
        self._dir = transcript_dir

    def enable(self) -> None:
        """Activate polling. Called when the service sees a Workflow tool_use,
        so the watcher only burns a glob per readline tick on sessions that
        actually run a workflow. Idempotent.

        Snapshots pre-existing wf_*.json (a resumed session's prior workflows)
        into _pre_existing so the live watcher doesn't re-emit them — those
        are history replay's job, and re-emitting a historical completed
        wf.json would falsely trip all_done before the new workflow starts
        (slice-036 bug: send ended the turn the instant it saw a stale
        completed wf.json, dropping the new workflow onto the next turn).
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
        """Drop cached mtimes so the next ``poll()`` re-reads every non-finished
        runId, even if its file hasn't changed.

        Called at ``send()`` entry: a workflow often outlives the turn that
        launched it (cc returns once the Workflow is in the background), so by
        the next send the wf.json may be terminal but its mtime hasn't moved
        since. Without resync the watcher would skip it (mtime unchanged) and
        the user would never see the completed/killed state on the live path.
        ``_finished`` runIds are kept (no point re-emitting a known-terminal
        snapshot every turn); history replay is the fallback for a page refresh.
        """
        self._last_mtime.clear()

    def close(self) -> None:
        """Drop all tracking state. Called when the CC session ends (service.close).

        ``_finished`` / ``_last_mtime`` are otherwise unbounded over a long
        session that runs many workflows — each entry is tiny (a runId string
        + a float), but close() makes the bound explicit.
        """
        self._last_mtime.clear()
        self._finished.clear()

    @property
    def enabled(self) -> bool:
        """True once ``enable()`` has been called (a Workflow was seen)."""
        return self._enabled

    @property
    def is_watching(self) -> bool:
        """True when there is at least one non-terminal runId still being
        polled (used by the service to know if it should keep calling poll)."""
        if self._dir is None:
            return False
        return len(self._last_mtime) > len(self._finished)

    @property
    def all_done(self) -> bool:
        """True when every workflow that appeared AFTER enable has reached a
        terminal state (pushed a terminal snapshot).

        service.send uses this to keep draining past cc's early ``result``
        (cc backgrounds the workflow and pushes ``result`` before it finishes)
        until the workflow actually completes — so the workflow's progress +
        cc's post-completion summary land on the CORRECT turn instead of
        lagging onto the next one (slice-036 bug3/4).

        Keys off ``_tracked`` (post-enable runIds), NOT ``_finished``: a
        resumed session has pre-existing completed wf.json files that would
        otherwise make ``_finished`` non-empty the instant the watcher enables,
        falsely reporting "all done" before the new workflow's wf.json even
        appears (slice-036 bug: send ended the turn on a stale completed
        wf.json, dropping the new workflow onto the next turn).
        """
        if not self._enabled:
            return True
        # workflow enabled but haven't seen its wf.json yet → keep draining
        if not self._tracked:
            return False
        return all(rid in self._finished for rid in self._tracked)

    def poll(self) -> list[WorkflowTreeEvent]:
        """Poll workflows/wf_*.json (completed) + tail each runId's
        journal.jsonl (live progress); emit a snapshot per changed runId.

        cc writes wf.json only ONCE the workflow finishes (verified:
        ctime == mtime == startTime+duration), so while it runs the only live
        signal is subagents/workflows/wf_<runId>/journal.jsonl's started/result
        events (agent joins / state flips). We tail those and emit a running
        snapshot with the agents seen so far; once wf.json appears we emit the
        full aggregate (which replaces it on the frontend).

        Returns:
            Zero or more WorkflowTreeEvent snapshots (one per changed runId),
            in sorted-runId order for determinism. Empty when nothing changed
            or no transcript dir is set.
        """
        if not self._enabled or self._dir is None:
            return []
        wf_dir = self._dir / "workflows"
        journal_root = self._dir / "subagents" / "workflows"
        # discover runIds: wf.json (completed) + journal dir (running, wf.json
        # not yet written)
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
        run_ids -= self._pre_existing  # skip historical (pre-enable) workflows
        if not run_ids:
            return []

        out: list[WorkflowTreeEvent] = []
        for run_id in sorted(run_ids):
            wf_path = wf_dir / f"{run_id}.json"
            if wf_path.is_file():
                try:
                    mtime = wf_path.stat().st_mtime
                except OSError:
                    # file vanished (cc rotated/cleaned) — drop tracking silently
                    self._last_mtime.pop(run_id, None)
                    continue
                if self._last_mtime.get(run_id) == mtime:
                    continue  # unchanged
                self._last_mtime[run_id] = mtime
                self._tracked.add(run_id)  # this turn's workflow
                snapshot = self._read_snapshot(run_id, wf_path)
                if snapshot is None:
                    continue
                out.append(snapshot)
                if snapshot.status in self._TERMINAL_STATUSES:
                    self._finished.add(run_id)
            else:
                # wf.json not written yet — tail journal for live progress
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
        """Tail ``<journal_root>/<run_id>/journal.jsonl`` and build a running
        snapshot from the started/result events seen so far (slice-036 P2).

        cc's journal carries only ``agentId`` + ``type`` (started|result) — no
        label / model / phase / tokens (those land in wf.json on completion).
        So the live snapshot uses agentId as the label and leaves phases empty;
        the full tree replaces it once wf.json appears. Idempotent: re-tails
        only bytes past the last offset, and re-emits the merged agent set each
        call so the frontend's replace-by-runId stays in sync.
        """
        journal_path = journal_root / run_id / "journal.jsonl"
        if not journal_path.is_file():
            return None
        offset = self._journal_offset.get(run_id, 0)
        agents = self._journal_agents.setdefault(run_id, {})
        try:
            with journal_path.open("rb") as fh:
                fh.seek(offset)
                for raw in fh:
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
                self._journal_offset[run_id] = fh.tell()
        except OSError as exc:
            logger.debug("journal tail failed (%s): %s", run_id, exc)
            return None
        if not agents:
            return None
        # cc may push journal `started` before the agent transcript lands on
        # disk; at that point label falls back to agentId. Retry each poll so
        # the label updates once the transcript's first line (the task prompt)
        # is writable — matches cc TUI showing the task promptly. Idempotent:
        # already-resolved labels (≠ agentId) are skipped, so no repeat IO.
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
        """Read agent-<id>.jsonl's first line, extract the task prompt, return
        a short label. None if transcript is absent/unreadable.

        cc's journal ``started`` event carries only agentId (no label); the
        agent's task prompt lives in the transcript's first user message (same
        source as wf.json's ``promptPreview``). Use it as the running label so
        the user sees what each subagent is doing before wf.json is written;
        the full parse replaces it with cc's label once wf.json appears.
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
                (c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"),
                "",
            )
        else:
            return None
        prompt = prompt.strip()
        if not prompt:
            return None
        return prompt[:40] + ("…" if len(prompt) > 40 else "")

    def _read_snapshot(
        self, run_id: str, path: Path
    ) -> WorkflowTreeEvent | None:
        """Read + parse one wf_<runId>.json; None on read/parse failure.

        Args:
            run_id: the runId (filename stem).
            path: path to workflows/wf_<runId>.json.

        Returns:
            The parsed WorkflowTreeEvent, or None if the file is unreadable /
            unparseable (half-written mid-append — will retry next poll).
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
        except Exception as exc:  # noqa: BLE001 — a bad file must not kill the turn
            logger.warning("workflow snapshot parse failed (%s): %s", run_id, exc)
            return None
