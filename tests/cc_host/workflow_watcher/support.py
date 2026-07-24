from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

WF_SAMPLE: dict[str, Any] = {
    "runId": "wf_test-123",
    "taskId": "task_abc",
    "workflowName": "baseline",
    "status": "completed",
    "agentCount": 3,
    "durationMs": 12345,
    "startTime": 1783425403342,
    "totalTokens": 1000,
    "totalToolCalls": 5,
    "defaultModel": "example-model",
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
            "agentId": "agent-1",
            "model": "example-model",
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
            "agentId": "agent-2",
            "model": "example-model",
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
            "agentId": "agent-3",
            "model": "example-model",
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


def sample() -> dict[str, Any]:
    return json.loads(json.dumps(WF_SAMPLE))


def write_wf(
    workflow_dir: Path,
    run_id: str,
    status: str = "running",
) -> Path:
    workflow_dir.mkdir(parents=True, exist_ok=True)
    path = workflow_dir / f"{run_id}.json"
    payload = sample()
    payload["runId"] = run_id
    payload["status"] = status
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def set_mtime(path: Path, value: float) -> None:
    os.utime(path, (value, value))


def write_journal(run_dir: Path, *events: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{json.dumps(event)}\n" for event in events)
    (run_dir / "journal.jsonl").write_text(content, encoding="utf-8")


def write_agent_prompt(
    run_dir: Path,
    agent_id: str,
    prompt: str,
) -> None:
    payload = {
        "type": "user",
        "message": {"role": "user", "content": prompt},
    }
    (run_dir / f"agent-{agent_id}.jsonl").write_text(
        f"{json.dumps(payload)}\n",
        encoding="utf-8",
    )
