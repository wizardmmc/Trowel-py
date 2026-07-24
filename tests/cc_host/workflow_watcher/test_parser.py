from __future__ import annotations

import pytest

from tests.cc_host.workflow_watcher.support import WF_SAMPLE, sample
from trowel_py.cc_host.workflow_watcher import (
    _agent_state_from_cc,
    parse_workflow_tree,
)
from trowel_py.schemas.cc_host import WorkflowTreeEvent


@pytest.mark.parametrize(
    ("cc_state", "expected"),
    [
        ("done", "done"),
        ("start", "running"),
        ("progress", "running"),
        ("error", "failed"),
        ("queued", "queued"),
        ("whatever-new", "running"),
        (None, "running"),
    ],
)
def test_agent_state_from_cc_maps_internal_to_wire(
    cc_state,
    expected,
) -> None:
    assert _agent_state_from_cc(cc_state) == expected


def test_parse_root_metadata_round_trips() -> None:
    event = parse_workflow_tree(WF_SAMPLE)
    assert isinstance(event, WorkflowTreeEvent)
    assert event.type == "workflow_tree"
    assert event.run_id == "wf_test-123"
    assert event.task_id == "task_abc"
    assert event.name == "baseline"
    assert event.args == "test question"
    assert event.status == "completed"
    assert event.agent_count == 3
    assert event.total_tokens == 1000
    assert event.total_tool_calls == 5
    assert event.duration_ms == 12345
    assert event.error is None


def test_parse_done_count_counts_done_agents() -> None:
    assert parse_workflow_tree(WF_SAMPLE).done_count == 1


def test_parse_phases_come_from_top_level_array_with_detail() -> None:
    phases = parse_workflow_tree(WF_SAMPLE).phases
    assert [(phase.title, phase.detail) for phase in phases] == [
        ("Scope", "decompose question"),
        ("Run", "parallel agents"),
    ]


def test_parse_agents_normalize_state_and_keep_fields() -> None:
    agents = parse_workflow_tree(WF_SAMPLE).agents
    by_id = {agent.agent_id: agent for agent in agents}
    assert len(agents) == 3
    assert by_id["agent-1"].state == "done"
    assert by_id["agent-1"].tokens == 100
    assert by_id["agent-1"].tool_calls == 1
    assert by_id["agent-1"].last_tool_name == "Bash"
    assert by_id["agent-1"].phase_index == 1
    assert by_id["agent-1"].phase_title == "Scope"
    assert by_id["agent-1"].prompt_preview == "p1"
    assert by_id["agent-1"].result_preview == "r1"
    assert by_id["agent-2"].state == "running"
    assert by_id["agent-3"].state == "running"


def test_parse_killed_workflow_surfaces_error() -> None:
    payload = sample()
    payload["status"] = "killed"
    payload["error"] = "Error: Workflow aborted"
    for item in payload["workflowProgress"]:
        if item.get("type") == "workflow_agent":
            item["state"] = "error" if item["agentId"] == "agent-1" else "progress"
    event = parse_workflow_tree(payload)
    assert event.status == "killed"
    assert event.error == "Error: Workflow aborted"
    assert event.done_count == 0
    assert event.agents[0].state == "failed"


def test_parse_args_dict_is_stringified() -> None:
    payload = sample()
    payload["args"] = {"question": "nested"}
    event = parse_workflow_tree(payload)
    assert isinstance(event.args, str)
    assert "nested" in event.args


def test_parse_missing_fields_do_not_crash() -> None:
    event = parse_workflow_tree(
        {
            "runId": "wf_x",
            "workflowName": "booting",
            "status": "running",
            "agentCount": 0,
        }
    )
    assert event.run_id == "wf_x"
    assert event.task_id is None
    assert event.args is None
    assert event.agent_count == 0
    assert event.done_count == 0
    assert event.phases == []
    assert event.agents == []
    assert event.total_tokens is None


def test_parse_drops_non_agent_non_phase_progress_events() -> None:
    payload = sample()
    payload["workflowProgress"].insert(
        0,
        {"type": "workflow_log", "message": "internal"},
    )
    payload["workflowProgress"].append(
        {"type": "agent_progress", "agentId": "agent-1"},
    )
    event = parse_workflow_tree(payload)
    assert len(event.agents) == 3
    assert len(event.phases) == 2


def test_parse_phases_fall_back_to_workflow_phase_events() -> None:
    payload = sample()
    payload["phases"] = None
    phases = parse_workflow_tree(payload).phases
    assert [phase.title for phase in phases] == ["Scope", "Run"]
    assert phases[0].detail is None


def test_parse_unknown_status_mapped_to_running() -> None:
    payload = sample()
    payload["status"] = "paused-unknown"
    assert parse_workflow_tree(payload).status == "running"
