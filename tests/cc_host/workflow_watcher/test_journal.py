from __future__ import annotations

from pathlib import Path

from tests.cc_host.workflow_watcher.support import (
    write_agent_prompt,
    write_journal,
    write_wf,
)
from trowel_py.cc_host.workflow_watcher import WorkflowWatcher


def test_watcher_journal_tail_emits_running_snapshot(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "session"
    run_dir = transcript / "subagents" / "workflows" / "wf_live"
    write_journal(
        run_dir,
        {"type": "started", "agentId": "agent-1", "key": "key-1"},
        {"type": "started", "agentId": "agent-2", "key": "key-2"},
        {"type": "result", "agentId": "agent-1", "key": "key-1"},
    )
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    snapshots = watcher.poll()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.run_id == "wf_live"
    assert snapshot.status == "running"
    assert snapshot.agent_count == 2
    assert snapshot.done_count == 1
    by_id = {agent.agent_id: agent for agent in snapshot.agents}
    assert by_id["agent-1"].state == "done"
    assert by_id["agent-2"].state == "running"
    assert by_id["agent-1"].label == "agent-1"
    assert snapshot.phases == []
    assert not watcher.all_done


def test_watcher_journal_then_wfjson_replaces(tmp_path: Path) -> None:
    transcript = tmp_path / "session"
    run_dir = transcript / "subagents" / "workflows" / "wf_x"
    write_journal(
        run_dir,
        {"type": "started", "agentId": "agent-1"},
    )
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    first = watcher.poll()
    assert first[0].status == "running"
    assert first[0].name == "wf_x"

    write_wf(
        transcript / "workflows",
        "wf_x",
        status="completed",
    )
    second = watcher.poll()
    assert second[0].status == "completed"
    assert second[0].name == "baseline"
    assert watcher.all_done


def test_journal_snapshot_label_from_prompt(tmp_path: Path) -> None:
    run_dir = tmp_path / "subagents" / "workflows" / "wf_label"
    write_journal(
        run_dir,
        {"type": "started", "agentId": "agent-1", "key": "key"},
    )
    write_agent_prompt(
        run_dir,
        "agent-1",
        "从代码可读性视角分析不可变数据结构，聚焦副作用与心智负担。",
    )
    watcher = WorkflowWatcher(tmp_path)
    watcher.set_transcript_dir(tmp_path)
    watcher.enable()
    agents = watcher.poll()[0].agents
    assert len(agents) == 1
    assert agents[0].label != "agent-1"
    assert "可读性" in agents[0].label


def test_journal_label_retried_after_transcript_late(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "subagents" / "workflows" / "wf_late"
    write_journal(
        run_dir,
        {"type": "started", "agentId": "agent-1", "key": "key"},
    )
    watcher = WorkflowWatcher(tmp_path)
    watcher.set_transcript_dir(tmp_path)
    watcher.enable()
    first = watcher.poll()
    assert first[0].agents[0].label == "agent-1"

    write_agent_prompt(
        run_dir,
        "agent-1",
        "研究缓存一致性术语的英文名称，为项目文档工作。",
    )
    second = watcher.poll()
    assert second[0].agents[0].label != "agent-1"
    assert "缓存一致性" in second[0].agents[0].label
