"""Tests for cc_host.workflow_watcher — parse cc's wf_<runId>.json into a
WorkflowTreeEvent (slice-036).

cc runs Workflows in the background and pushes no progress to its stream-json
stdout; trowel reads the on-disk wf_<runId>.json cc maintains (the single
source of truth) and translates it. These tests pin the pure translation:
shapes are verified against a real deep-research run (runId wf_a5daf5bf-47b)
via spikes — see wiki/raw/2026-07-07-tcc-workflow-render-bug.md.

The translation is a pure function: dict in -> WorkflowTreeEvent out, no IO.
The stat-poll / tail lifecycle lives on the WorkflowWatcher class (covered by
service-level tests).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trowel_py.cc_host.workflow_watcher import (
    WorkflowWatcher,
    _agent_state_from_cc,
    parse_workflow_tree,
)
from trowel_py.schemas.cc_host import WorkflowAgentInfo, WorkflowTreeEvent


# ── realistic minimal sample (shape from real wf_a5daf5bf-47b.json) ──
# 3 agents across 2 phases, mixing done / progress / start so the state
# mapping + done_count are both exercised.
WF_SAMPLE: dict = {
    "runId": "wf_test-123",
    "taskId": "task_abc",
    "workflowName": "baseline",
    "status": "completed",
    "agentCount": 3,
    "durationMs": 12345,
    "startTime": 1783425403342,
    "totalTokens": 1000,
    "totalToolCalls": 5,
    "defaultModel": "glm-5.1",
    "args": "test question",
    "error": None,
    "result": None,
    "phases": [
        {"title": "Scope", "detail": "decompose question"},
        {"title": "Run", "detail": "parallel agents"},
    ],
    "workflowProgress": [
        {"type": "workflow_phase", "index": 1, "title": "Scope"},
        {"type": "workflow_phase", "index": 2, "title": "Run"},
        {
            "type": "workflow_agent",
            "index": 1,
            "label": "scope",
            "phaseIndex": 1,
            "phaseTitle": "Scope",
            "agentId": "a1",
            "model": "glm-5.1",
            "state": "done",
            "tokens": 100,
            "toolCalls": 1,
            "lastToolName": "Bash",
            "durationMs": 1000,
            "promptPreview": "p1",
            "resultPreview": "r1",
        },
        {
            "type": "workflow_agent",
            "index": 2,
            "label": "run:a",
            "phaseIndex": 2,
            "phaseTitle": "Run",
            "agentId": "a2",
            "model": "glm-5.1",
            "state": "progress",
            "tokens": 50,
            "toolCalls": 0,
            "lastToolName": None,
            "durationMs": None,
            "promptPreview": "p2",
            "resultPreview": None,
        },
        {
            "type": "workflow_agent",
            "index": 3,
            "label": "run:b",
            "phaseIndex": 2,
            "phaseTitle": "Run",
            "agentId": "a3",
            "model": "glm-5.1",
            "state": "start",
            "tokens": 0,
            "toolCalls": 0,
            "lastToolName": None,
            "durationMs": None,
            "promptPreview": None,
            "resultPreview": None,
        },
    ],
}


# ── state mapping: cc internal -> trowel wire ──

@pytest.mark.parametrize(
    "cc_state,expected",
    [
        ("done", "done"),
        ("start", "running"),
        ("progress", "running"),
        ("error", "failed"),
        ("queued", "queued"),
        # cc may rename/introduce a state; unknown falls back to running so the
        # node shows as not-finished rather than silently disappearing.
        ("whatever-new", "running"),
        (None, "running"),
    ],
)
def test_agent_state_from_cc_maps_internal_to_wire(cc_state, expected):
    assert _agent_state_from_cc(cc_state) == expected


# ── parse_workflow_tree: the core translation ──

def test_parse_root_metadata_round_trips():
    """Top-level scalars (runId/taskId/name/status/counts) map 1:1."""
    ev = parse_workflow_tree(WF_SAMPLE)
    assert isinstance(ev, WorkflowTreeEvent)
    assert ev.type == "workflow_tree"
    assert ev.run_id == "wf_test-123"
    assert ev.task_id == "task_abc"
    assert ev.name == "baseline"
    assert ev.args == "test question"
    assert ev.status == "completed"
    assert ev.agent_count == 3
    assert ev.total_tokens == 1000
    assert ev.total_tool_calls == 5
    assert ev.duration_ms == 12345
    assert ev.error is None


def test_parse_done_count_counts_done_agents():
    """done_count = #agents with state=='done' (the progress bar numerator).
    Sample has 1 done, 2 in-flight → 1/3."""
    ev = parse_workflow_tree(WF_SAMPLE)
    assert ev.done_count == 1


def test_parse_phases_come_from_top_level_array_with_detail():
    """phases carry detail from the TOP-LEVEL phases[] (workflowProgress's
    workflow_phase events have NO detail — only {type,index,title})."""
    ev = parse_workflow_tree(WF_SAMPLE)
    assert len(ev.phases) == 2
    assert ev.phases[0].title == "Scope"
    assert ev.phases[0].detail == "decompose question"
    assert ev.phases[1].title == "Run"
    assert ev.phases[1].detail == "parallel agents"


def test_parse_agents_normalize_state_and_keep_fields():
    """Each workflow_agent becomes a WorkflowAgentInfo; cc's start/progress
    both normalize to 'running'."""
    ev = parse_workflow_tree(WF_SAMPLE)
    assert len(ev.agents) == 3
    by_id = {a.agent_id: a for a in ev.agents}
    assert by_id["a1"].state == "done"
    assert by_id["a1"].tokens == 100
    assert by_id["a1"].tool_calls == 1
    assert by_id["a1"].last_tool_name == "Bash"
    assert by_id["a1"].phase_index == 1
    assert by_id["a1"].phase_title == "Scope"
    assert by_id["a1"].prompt_preview == "p1"
    assert by_id["a1"].result_preview == "r1"
    # progress -> running, start -> running
    assert by_id["a2"].state == "running"
    assert by_id["a3"].state == "running"


def test_parse_killed_workflow_surfaces_error():
    """killed/failed status carries the error string (C-5: error must be
    visible). A killed run's in-flight agents stay non-done."""
    sample = json.loads(json.dumps(WF_SAMPLE))
    sample["status"] = "killed"
    sample["error"] = "Error: Workflow aborted at S (cli.js:4390:6612)"
    # one agent errored, two still in-flight (real killed-run shape)
    for a in sample["workflowProgress"]:
        if a.get("type") == "workflow_agent":
            if a["agentId"] == "a1":
                a["state"] = "error"
            else:
                a["state"] = "progress"
    ev = parse_workflow_tree(sample)
    assert ev.status == "killed"
    assert ev.error is not None
    assert "Workflow aborted" in ev.error
    assert ev.done_count == 0
    assert ev.agents[0].state == "failed"  # error -> failed


def test_parse_args_dict_is_stringified():
    """args is normally the question string; if cc ever writes a dict/object
    there, stringify rather than crash (frontend renders it as text)."""
    sample = json.loads(json.dumps(WF_SAMPLE))
    sample["args"] = {"question": "nested"}
    ev = parse_workflow_tree(sample)
    assert isinstance(ev.args, str)
    assert "nested" in ev.args


def test_parse_missing_fields_do_not_crash():
    """A half-written wf.json (workflow still booting) must not blow up —
    every optional field degrades to None / empty lists."""
    minimal = {
        "runId": "wf_x",
        "workflowName": "booting",
        "status": "running",
        "agentCount": 0,
        # no taskId / args / phases / workflowProgress / counts
    }
    ev = parse_workflow_tree(minimal)
    assert ev.run_id == "wf_x"
    assert ev.task_id is None
    assert ev.args is None
    assert ev.agent_count == 0
    assert ev.done_count == 0
    assert ev.phases == []
    assert ev.agents == []
    assert ev.total_tokens is None


def test_parse_drops_non_agent_non_phase_progress_events():
    """workflowProgress may carry workflow_log / agent_progress / etc. (cc's
    internal reducer types). Only workflow_phase / workflow_agent are relevant
    to the tree; others are ignored without error."""
    sample = json.loads(json.dumps(WF_SAMPLE))
    sample["workflowProgress"].insert(
        0,
        {"type": "workflow_log", "message": "something internal"},
    )
    sample["workflowProgress"].append(
        {"type": "agent_progress", "agentId": "a1", "tool": "Bash"},
    )
    ev = parse_workflow_tree(sample)
    assert len(ev.agents) == 3  # unchanged
    assert len(ev.phases) == 2


def test_parse_phases_fall_back_to_workflow_phase_events():
    """If the top-level phases[] is absent (older/cc variant), phases are
    rebuilt from the workflow_phase progress events (index orders them)."""
    sample = json.loads(json.dumps(WF_SAMPLE))
    sample["phases"] = None
    ev = parse_workflow_tree(sample)
    assert [p.title for p in ev.phases] == ["Scope", "Run"]
    # no detail available from workflow_phase events
    assert ev.phases[0].detail is None


def test_parse_unknown_status_mapped_to_running():
    """An unrecognized status string degrades to 'running' (keep the card
    live + polling) rather than dropping the event."""
    sample = json.loads(json.dumps(WF_SAMPLE))
    sample["status"] = "paused-unknown"
    ev = parse_workflow_tree(sample)
    assert ev.status == "running"


# ── WorkflowWatcher: the live IO layer ──
#
# stat-polls workflows/wf_*.json under a transcript dir. These tests build a
# fake transcript dir in tmp_path and drive poll() through the lifecycle:
# enable gating → discovery → mtime dedup → terminal stop → vanish handling.

def _write_wf(wf_dir: Path, run_id: str, status: str = "running") -> Path:
    """Write a minimal wf_<runId>.json under wf_dir; return its path."""
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / f"{run_id}.json"
    payload = json.loads(json.dumps(WF_SAMPLE))
    payload["runId"] = run_id
    payload["status"] = status
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _set_mtime(path: Path, t: float) -> None:
    """Force a file's mtime (st_mtime second-granularity on some FS)."""
    os.utime(path, (t, t))


def test_watcher_inert_until_enabled(tmp_path: Path):
    """Not reported until enable(), AND a wf.json that exists before enable()
    is treated as historical (pre-existing) — only one appearing AFTER enable
    is pushed (slice-036: resumed session's stale wf.json must not re-emit)."""
    transcript = tmp_path / "sess"
    wf_dir = transcript / "workflows"
    w = WorkflowWatcher(transcript)
    assert w.poll() == []  # not enabled yet
    w.enable()
    assert w.poll() == []  # enabled but no wf.json yet
    _write_wf(wf_dir, "wf_a")  # workflow appears after enable
    snaps = w.poll()
    assert len(snaps) == 1
    assert snaps[0].run_id == "wf_a"


def test_watcher_dedups_unchanged_file(tmp_path: Path):
    """A second poll with no mtime change emits nothing."""
    transcript = tmp_path / "sess"
    wf_dir = transcript / "workflows"
    w = WorkflowWatcher(transcript)
    w.enable()
    path = _write_wf(wf_dir, "wf_a")
    _set_mtime(path, 1000.0)
    assert len(w.poll()) == 1
    # same mtime → no new snapshot
    assert w.poll() == []


def test_watcher_emits_on_mtime_change(tmp_path: Path):
    """When cc rewrites wf.json (mtime advances), a fresh snapshot is emitted."""
    transcript = tmp_path / "sess"
    wf_dir = transcript / "workflows"
    w = WorkflowWatcher(transcript)
    w.enable()
    path = _write_wf(wf_dir, "wf_a", status="running")
    _set_mtime(path, 1000.0)
    first = w.poll()
    assert first[0].status == "running"
    # cc finishes the workflow, rewrites the file
    _write_wf(wf_dir, "wf_a", status="completed")
    _set_mtime(path, 2000.0)
    second = w.poll()
    assert len(second) == 1
    assert second[0].status == "completed"


def test_watcher_stops_polling_after_terminal_snapshot(tmp_path: Path):
    """Once a terminal snapshot is emitted, the runId is not re-polled even if
    its mtime later changes (cc stops rewriting a finished workflow's file)."""
    transcript = tmp_path / "sess"
    wf_dir = transcript / "workflows"
    w = WorkflowWatcher(transcript)
    w.enable()
    path = _write_wf(wf_dir, "wf_a", status="completed")
    _set_mtime(path, 1000.0)
    assert len(w.poll()) == 1
    # mtime bumps but status already terminal → no new snapshot
    _set_mtime(path, 3000.0)
    assert w.poll() == []
    assert not w.is_watching


def test_watcher_discovers_new_runid_mid_run(tmp_path: Path):
    """A second workflow launched while one is already tracked is picked up
    on the next poll (C-6 multi-workflow concurrency)."""
    transcript = tmp_path / "sess"
    wf_dir = transcript / "workflows"
    _write_wf(wf_dir, "wf_a")
    w = WorkflowWatcher(transcript)
    w.enable()
    w.poll()
    # a second workflow lands
    _write_wf(wf_dir, "wf_b")
    snaps = w.poll()
    ids = sorted(s.run_id for s in snaps)
    assert ids == ["wf_b"]  # wf_a unchanged (mtime same), wf_b is new


def test_watcher_vanished_file_dropped_silently(tmp_path: Path):
    """If cc removes a wf.json mid-run (rotation/cleanup), the next poll drops
    it from tracking instead of erroring."""
    transcript = tmp_path / "sess"
    wf_dir = transcript / "workflows"
    path = _write_wf(wf_dir, "wf_a")
    _set_mtime(path, 1000.0)
    w = WorkflowWatcher(transcript)
    w.enable()
    w.poll()
    path.unlink()
    assert w.poll() == []  # no crash


def test_watcher_no_transcript_dir_yields_nothing(tmp_path: Path):
    """Pre-init (no cc_session_id yet) → no transcript dir → poll is a no-op."""
    w = WorkflowWatcher(None)
    w.enable()
    assert w.poll() == []


def test_watcher_skips_pre_existing_and_does_not_trip_all_done(tmp_path: Path):
    """slice-036 bug A/B/D: a resumed session has historical completed wf.json.
    enable() must snapshot them as pre-existing — neither re-emit nor count
    toward all_done. Otherwise a stale completed wf.json trips all_done the
    instant the watcher enables, ending the turn before the new workflow's
    wf.json even appears (new workflow drops onto the next turn)."""
    transcript = tmp_path / "sess"
    wf_dir = transcript / "workflows"
    # historical completed wf.json (from a prior run on this resumed session)
    _write_wf(wf_dir, "wf_old", status="completed")
    _set_mtime(wf_dir / "wf_old.json", 500.0)
    w = WorkflowWatcher(transcript)
    w.enable()
    assert w.poll() == []          # historical wf NOT re-emitted
    assert not w.all_done          # no tracked workflow yet → keep draining
    # new workflow appears (this turn's)
    _write_wf(wf_dir, "wf_new", status="running")
    snaps = w.poll()
    assert len(snaps) == 1
    assert snaps[0].run_id == "wf_new"
    assert not w.all_done          # running → not done
    # new workflow completes
    _write_wf(wf_dir, "wf_new", status="completed")
    _set_mtime(wf_dir / "wf_new.json", 2000.0)
    w.poll()
    assert w.all_done              # tracked workflow finished


def test_watcher_journal_tail_emits_running_snapshot(tmp_path: Path):
    """slice-036 P2: while a workflow runs, cc hasn't written wf.json yet —
    the watcher tails journal.jsonl (started/result) and emits a running
    snapshot with the agents seen so far (label falls back to agentId)."""
    transcript = tmp_path / "sess"
    jdir = transcript / "subagents" / "workflows" / "wf_live"
    jdir.mkdir(parents=True)
    (jdir / "journal.jsonl").write_text(
        json.dumps({"type": "started", "agentId": "a1", "key": "k1"}) + "\n"
        + json.dumps({"type": "started", "agentId": "a2", "key": "k2"}) + "\n"
        + json.dumps({"type": "result", "agentId": "a1", "key": "k1"}) + "\n"
    )
    w = WorkflowWatcher(transcript)
    w.enable()
    snaps = w.poll()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.run_id == "wf_live"
    assert s.status == "running"
    assert s.agent_count == 2
    assert s.done_count == 1  # a1 done, a2 running
    by_id = {a.agent_id: a for a in s.agents}
    assert by_id["a1"].state == "done"
    assert by_id["a2"].state == "running"
    assert by_id["a1"].label == "a1"  # label falls back to agentId
    assert s.phases == []  # no wf.json yet
    assert not w.all_done  # running, not finished


def test_watcher_journal_then_wfjson_replaces(tmp_path: Path):
    """Once wf.json appears (workflow completed), the full snapshot replaces
    the journal-based running one (same run_id → frontend upserts)."""
    transcript = tmp_path / "sess"
    jdir = transcript / "subagents" / "workflows" / "wf_x"
    jdir.mkdir(parents=True)
    (jdir / "journal.jsonl").write_text(
        json.dumps({"type": "started", "agentId": "a1"}) + "\n"
    )
    w = WorkflowWatcher(transcript)
    w.enable()
    first = w.poll()
    assert first[0].status == "running"
    assert first[0].name == "wf_x"  # placeholder before wf.json
    # wf.json appears (completed) — full snapshot replaces
    _write_wf(transcript / "workflows", "wf_x", status="completed")
    second = w.poll()
    assert second[0].status == "completed"
    assert second[0].name == "baseline"  # from WF_SAMPLE
    assert w.all_done


def test_journal_snapshot_label_from_prompt(tmp_path: Path) -> None:
    """slice-036 P3: running snapshot 的 agent label 从 agent transcript 首行
    prompt 摘要获取,不是 agentId。让用户实时看到 subagent 在跑什么(实测:
    journal started 只有 agentId,但 agent-<id>.jsonl 首行含完整 prompt)。"""
    import json as _json
    run_id = "wf_lbltest"
    run_dir = tmp_path / "subagents" / "workflows" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "journal.jsonl").write_text(
        _json.dumps({"type": "started", "agentId": "a1", "key": "k"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "agent-a1.jsonl").write_text(
        _json.dumps({
            "type": "user",
            "message": {"role": "user",
                        "content": "从代码可读性的视角,分析不可变数据结构的安全优势,聚焦副作用与心智负担。"},
        }) + "\n",
        encoding="utf-8",
    )
    w = WorkflowWatcher(tmp_path)
    w.set_transcript_dir(tmp_path)
    w.enable()
    events = w.poll()
    assert events, "running snapshot should be emitted"
    agents = events[0].agents
    assert len(agents) == 1
    assert agents[0].label != "a1", (
        f"label 应是 prompt 摘要不是 agentId;实际 {agents[0].label!r}"
    )
    assert "可读性" in agents[0].label, (
        f"label 应含 prompt 关键字;实际 {agents[0].label!r}"
    )


def test_journal_label_retried_after_transcript_late(tmp_path: Path) -> None:
    """slice-036: cc 可能先推 journal started 再写 agent transcript。started 时
    transcript 没就绪 → label 暂用 agentId 并缓存;transcript 落盘后,后续 poll
    重试,label 更新成 prompt 摘要(用户实测:有的 agent 显示 prompt 有的只显
    agentId,正是此时序差)。"""
    import json as _json
    run_id = "wf_late"
    run_dir = tmp_path / "subagents" / "workflows" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "journal.jsonl").write_text(
        _json.dumps({"type": "started", "agentId": "a1", "key": "k"}) + "\n",
        encoding="utf-8",
    )
    w = WorkflowWatcher(tmp_path)
    w.set_transcript_dir(tmp_path)
    w.enable()
    ev1 = w.poll()
    assert ev1, "first snapshot should emit"
    assert ev1[0].agents[0].label == "a1", (
        "transcript 未就绪 → label 暂用 agentId"
    )
    # 后来 transcript 落盘(cc 写了 user message)
    (run_dir / "agent-a1.jsonl").write_text(
        _json.dumps({"type": "user", "message": {"role": "user",
            "content": "研究电信诈骗术语的英文名称,为 SP 论文工作。"}}) + "\n",
        encoding="utf-8",
    )
    ev2 = w.poll()
    assert ev2, "second snapshot should emit"
    assert ev2[0].agents[0].label != "a1", (
        f"transcript 落盘后重试,label 应更新;实际 {ev2[0].agents[0].label!r}"
    )
    assert "电信诈骗" in ev2[0].agents[0].label, (
        f"label 应含 prompt 关键字;实际 {ev2[0].agents[0].label!r}"
    )
