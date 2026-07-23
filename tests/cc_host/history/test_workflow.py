from __future__ import annotations

import json
from pathlib import Path

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import (
    ToolCallEvent,
    UserEvent,
    WorkflowTreeEvent,
)
from tests.cc_host.history._support import (
    _assistant,
    _init,
    _result_success,
    _user_text,
    _workflow_tool_use,
    _write_jsonl,
    _write_workflow_json,
    wf_minimal_payload,
)

# fixture 结构取自真实 deep-research 记录，回放需保持与实时完成态相同的事件顺序。


def test_parse_history_injects_workflow_after_tool_use(
    fake_projects: Path,
) -> None:
    sid = "abc-wf"
    sid_dir = fake_projects / sid
    sid_dir.mkdir()
    _write_workflow_json(sid_dir, run_id="wf_x", status="completed")
    _write_jsonl(
        fake_projects / f"{sid}.jsonl",
        [_init(), _user_text("go"), _workflow_tool_use(), _result_success()],
    )

    events = history.parse_history("/workdir", sid)

    wf_events = [e for e in events if isinstance(e, WorkflowTreeEvent)]
    assert len(wf_events) == 1
    assert wf_events[0].run_id == "wf_x"
    assert wf_events[0].status == "completed"
    assert wf_events[0].agent_count == 1
    assert len(wf_events[0].phases) == 1

    tool_idx = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, ToolCallEvent) and e.tool_name == "Workflow"
    )
    assert events[tool_idx + 1] is wf_events[0]


def test_parse_history_no_workflow_dir_no_injection(
    fake_projects: Path,
) -> None:
    sid = "abc-nodir"
    _write_jsonl(
        fake_projects / f"{sid}.jsonl",
        [_init(), _user_text("go"), _workflow_tool_use(), _result_success()],
    )

    events = history.parse_history("/workdir", sid)

    assert not any(isinstance(e, WorkflowTreeEvent) for e in events)


def test_parse_history_orphan_workflow_appended_at_end(
    fake_projects: Path,
) -> None:
    sid = "abc-orphan"
    sid_dir = fake_projects / sid
    sid_dir.mkdir()
    _write_workflow_json(sid_dir, run_id="wf_orphan", status="killed")
    _write_jsonl(
        fake_projects / f"{sid}.jsonl",
        [
            _init(),
            _user_text("go"),
            _assistant([{"type": "text", "text": "done"}]),
            _result_success(),
        ],
    )

    events = history.parse_history("/workdir", sid)

    wf_events = [e for e in events if isinstance(e, WorkflowTreeEvent)]
    assert len(wf_events) == 1
    assert wf_events[0].status == "killed"

    assert events[-1] is wf_events[0]


def test_parse_history_multiple_workflows_all_injected(
    fake_projects: Path,
) -> None:
    sid = "abc-multi"
    sid_dir = fake_projects / sid
    sid_dir.mkdir()

    for rid, start in (("wf_b", 1000), ("wf_a", 2000)):
        d = wf_minimal_payload(rid, start=start)
        (sid_dir / "workflows" / f"{rid}.json").parent.mkdir(
            parents=True, exist_ok=True
        )
        (sid_dir / "workflows" / f"{rid}.json").write_text(
            json.dumps(d), encoding="utf-8"
        )
    _write_jsonl(
        fake_projects / f"{sid}.jsonl",
        [_init(), _user_text("go"), _workflow_tool_use(), _result_success()],
    )

    events = history.parse_history("/workdir", sid)
    wf_events = [e for e in events if isinstance(e, WorkflowTreeEvent)]
    assert [e.run_id for e in wf_events] == ["wf_b", "wf_a"]


def test_parse_history_drops_task_notification_user_row(
    fake_projects: Path,
) -> None:
    sid = "abc-tn"
    _write_jsonl(
        fake_projects / f"{sid}.jsonl",
        [
            _init(),
            _user_text("hi"),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "<task-notification>\n<task-id>w1e</task-id>\n</task-notification>",
                },
                "timestamp": "2026-07-08T00:00:00Z",
            },
            _result_success(),
        ],
    )
    events = history.parse_history("/workdir", sid)
    user_texts = [e.text for e in events if isinstance(e, UserEvent)]
    assert "hi" in user_texts
    assert not any("task-notification" in t for t in user_texts)


def test_parse_history_each_tool_use_gets_own_workflow(fake_projects: Path) -> None:
    sid = "abc-multi-turn"
    sid_dir = fake_projects / sid
    sid_dir.mkdir()
    _write_workflow_json(sid_dir, run_id="wf_1", name="first")
    _write_workflow_json(sid_dir, run_id="wf_2", name="second")
    _write_jsonl(
        fake_projects / f"{sid}.jsonl",
        [
            _init(),
            _user_text("go1"),
            _workflow_tool_use(name="first"),
            _assistant([{"type": "text", "text": "done1"}]),
            _result_success(),
            _user_text("go2"),
            _workflow_tool_use(name="second"),
            _assistant([{"type": "text", "text": "done2"}]),
            _result_success(),
        ],
    )
    events = history.parse_history("/workdir", sid)
    wf_events = [e for e in events if isinstance(e, WorkflowTreeEvent)]
    assert len(wf_events) == 2

    def _tu_idx(name: str) -> int:
        return next(
            i
            for i, e in enumerate(events)
            if isinstance(e, ToolCallEvent)
            and e.tool_name == "Workflow"
            and e.input.get("name") == name
        )

    after1 = events[_tu_idx("first") + 1]
    after2 = events[_tu_idx("second") + 1]
    assert isinstance(after1, WorkflowTreeEvent) and after1.run_id == "wf_1"
    assert isinstance(after2, WorkflowTreeEvent) and after2.run_id == "wf_2"
