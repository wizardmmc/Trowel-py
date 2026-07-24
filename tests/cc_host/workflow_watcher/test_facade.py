from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from tests.cc_host.workflow_watcher.support import sample, write_wf
from trowel_py.cc_host import workflow_watcher
from trowel_py.cc_host.workflow_watcher import WorkflowWatcher

PARSER_NAMES = (
    "_agent_state_from_cc",
    "_status_from_cc",
    "_args_to_str",
    "_int_or_none",
    "_str_or_none",
    "_phase_from_top",
    "_phases_from_progress",
    "_agent_from_event",
    "parse_workflow_tree",
)


def test_parser_facade_signatures_and_modules_are_stable() -> None:
    expected = {
        "_agent_state_from_cc": "(cc_state: 'Any') -> 'WireState'",
        "_status_from_cc": "(cc_status: 'Any') -> 'WireStatus'",
        "_args_to_str": "(raw: 'Any') -> 'str | None'",
        "_int_or_none": "(value: 'Any') -> 'int | None'",
        "_str_or_none": "(value: 'Any') -> 'str | None'",
        "_phase_from_top": "(p: 'Any') -> 'WorkflowPhaseInfo | None'",
        "_phases_from_progress": ("(events: 'list[Any]') -> 'list[WorkflowPhaseInfo]'"),
        "_agent_from_event": "(e: 'Any') -> 'WorkflowAgentInfo | None'",
        "parse_workflow_tree": ("(wf: 'dict[str, Any]') -> 'WorkflowTreeEvent'"),
    }
    for name in PARSER_NAMES:
        value = getattr(workflow_watcher, name)
        assert value.__module__ == workflow_watcher.__name__
        assert str(inspect.signature(value)) == expected[name]


def test_parser_facade_injects_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = {}

    def run(workflow, **dependencies):
        observed["workflow"] = workflow
        observed.update(dependencies)
        return "sentinel"

    monkeypatch.setattr(
        workflow_watcher,
        "_run_parse_workflow_tree",
        run,
    )
    payload = sample()
    assert workflow_watcher.parse_workflow_tree(payload) == "sentinel"
    assert observed["workflow"] is payload
    assert observed["event_type"] is workflow_watcher.WorkflowTreeEvent
    assert observed["phase_from_top_entry"] is workflow_watcher._phase_from_top
    assert observed["phases_from_events"] is workflow_watcher._phases_from_progress
    assert observed["agent_from_progress"] is workflow_watcher._agent_from_event
    assert observed["normalize_status"] is workflow_watcher._status_from_cc
    assert observed["stringify_args"] is workflow_watcher._args_to_str
    assert observed["to_optional_int"] is workflow_watcher._int_or_none
    assert observed["to_optional_str"] is workflow_watcher._str_or_none


def test_helper_facades_inject_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = {}
    sentinel = object()

    def capture(name):
        def run(value, **dependencies):
            observed[name] = (value, dependencies)
            return sentinel

        return run

    cases: tuple[tuple[str, Any, Any, dict[str, Any]], ...] = (
        (
            "_run_agent_state_from_cc",
            workflow_watcher._agent_state_from_cc,
            "state",
            {"state_map": workflow_watcher._AGENT_STATE_MAP},
        ),
        (
            "_run_status_from_cc",
            workflow_watcher._status_from_cc,
            "status",
            {},
        ),
        (
            "_run_args_to_str",
            workflow_watcher._args_to_str,
            {"question": "example"},
            {"dumps": workflow_watcher.json.dumps},
        ),
        (
            "_run_int_or_none",
            workflow_watcher._int_or_none,
            "3",
            {},
        ),
        (
            "_run_str_or_none",
            workflow_watcher._str_or_none,
            3,
            {},
        ),
        (
            "_run_phase_from_top",
            workflow_watcher._phase_from_top,
            {"title": "phase"},
            {
                "phase_type": workflow_watcher.WorkflowPhaseInfo,
                "to_optional_str": workflow_watcher._str_or_none,
            },
        ),
        (
            "_run_phases_from_progress",
            workflow_watcher._phases_from_progress,
            [],
            {
                "phase_type": workflow_watcher.WorkflowPhaseInfo,
                "to_optional_int": workflow_watcher._int_or_none,
                "to_optional_str": workflow_watcher._str_or_none,
            },
        ),
        (
            "_run_agent_from_event",
            workflow_watcher._agent_from_event,
            {"agentId": "agent-1"},
            {
                "agent_type": workflow_watcher.WorkflowAgentInfo,
                "state_from_cc": workflow_watcher._agent_state_from_cc,
                "to_optional_int": workflow_watcher._int_or_none,
                "to_optional_str": workflow_watcher._str_or_none,
            },
        ),
    )

    for runner_name, facade, value, dependencies in cases:
        key = runner_name.removeprefix("_run_")
        monkeypatch.setattr(
            workflow_watcher,
            runner_name,
            capture(key),
        )
        assert facade(value) is sentinel
        assert observed[key] == (value, dependencies)


def test_watcher_read_snapshot_uses_runtime_facade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_wf(tmp_path, "wf_patch")
    sentinel = object()
    observed = []

    def parse(payload):
        observed.append(payload)
        return sentinel

    monkeypatch.setattr(workflow_watcher, "parse_workflow_tree", parse)
    watcher = WorkflowWatcher(tmp_path)
    assert watcher._read_snapshot("wf_patch", path) is sentinel
    assert observed[0]["runId"] == "wf_patch"
