from __future__ import annotations

import json
from pathlib import Path


def _write_jsonl(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _user_text(text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "timestamp": "2026-06-01T00:00:00Z",
    }


def _assistant(blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": blocks},
        "timestamp": "2026-06-01T00:00:05Z",
    }


def _tool_result(tool_use_id: str, content: str) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
            ],
        },
        "timestamp": "2026-06-01T00:00:10Z",
    }


def _init() -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "model": "glm-5.2",
        "cwd": "/tmp",
        "session_id": "abc-123",
        "tools": ["Read", "Write", "Bash"],
    }


def _result_success() -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "num_turns": 1,
    }


def _tool_result_with_tur(tool_use_id: str, content: str, tur: dict) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
            ],
        },
        "toolUseResult": tur,
    }


def _user_list_text(text: str, is_meta: bool = False) -> dict:
    ev: dict = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "timestamp": "2026-06-01T00:00:00Z",
    }
    if is_meta:
        ev["isMeta"] = True
    return ev


def _ts_entry(type_: str, timestamp: str, content) -> dict:
    return {
        "type": type_,
        "timestamp": timestamp,
        "message": {"role": type_, "content": content},
    }


def _workflow_tool_use(
    name: str = "baseline", args: str = "question", tool_use_id: str = "call_wf_1"
) -> dict:
    return _assistant(
        [
            {
                "type": "tool_use",
                "id": tool_use_id,
                "name": "Workflow",
                "input": {"name": name, "args": args},
            }
        ]
    )


def _write_workflow_json(
    sid_dir: Path,
    run_id: str = "wf_x",
    status: str = "completed",
    name: str = "baseline",
) -> None:
    wf_dir = sid_dir / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "runId": run_id,
        "taskId": "task_x",
        "workflowName": name,
        "status": status,
        "agentCount": 1,
        "totalTokens": 100,
        "totalToolCalls": 1,
        "durationMs": 999,
        "args": "question",
        "phases": [{"title": "Scope", "detail": "decompose question"}],
        "workflowProgress": [
            {"type": "workflow_phase", "index": 1, "title": "Scope"},
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
            },
        ],
    }
    (wf_dir / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def wf_minimal_payload(run_id: str, start: int = 0) -> dict:
    return {
        "runId": run_id,
        "workflowName": "baseline",
        "status": "completed",
        "agentCount": 0,
        "startTime": start,
    }
