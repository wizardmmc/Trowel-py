"""真实运行时工作台 smoke；默认排除，不读取生产 Model OS 数据。"""

from __future__ import annotations

import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app


pytestmark = pytest.mark.integration

_CC_ENABLED = os.environ.get("CC_INTEGRATION") == "1"
_CODEX_ENABLED = os.environ.get("CODEX_INTEGRATION") == "1"


@pytest.fixture
def isolated_runtime_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    async def skip_unrelated_maintenance(_self) -> None:
        return None

    memory_root = tmp_path / "memory"
    (memory_root / "meta").mkdir(parents=True)
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: memory_root,
    )
    monkeypatch.setenv(
        "TROWEL_AGENT_SESSIONS_PATH",
        str(tmp_path / "agent-sessions.json"),
    )
    monkeypatch.setenv(
        "TROWEL_MCP_CONFIG",
        str(tmp_path / "memory-mcp-config.json"),
    )
    monkeypatch.setenv("TROWEL_CHECKPOINT_ENABLE", "0")
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.scheduler.MemoryReviewScheduler.start",
        skip_unrelated_maintenance,
    )
    monkeypatch.setattr(
        "trowel_py.memory.profile_distill.scheduler.ProfileDistillScheduler.start",
        skip_unrelated_maintenance,
    )
    monkeypatch.setattr(
        "trowel_py.memory.tidy_scheduler.TidyScheduler.start",
        skip_unrelated_maintenance,
    )
    return tmp_path


def _sse_events(response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line.removeprefix("data: ")))
    return events


def _terminal_types(response) -> set[str]:
    return {
        str(event.get("type"))
        for event in _sse_events(response)
        if event.get("type") in {"finished", "error", "interrupted", "session_exited"}
    }


def _start_task(
    client: TestClient,
    *,
    runtime: str,
    permission: str,
    workdir: Path,
) -> tuple[str, str]:
    store = client.app.state.model_os_store
    assert store is not None
    task = store.create_task_from_user_request(
        original_goal=(
            "这是一次隔离的工作台运行时验证。"
            "第一轮只回复 WORKBENCH_READY，不调用工具，不修改文件。"
        ),
        idempotency_key=f"workbench-smoke-create-{runtime}",
    )
    warmed = client.post(
        f"/api/model-os/tasks/{task.task_id}/warm",
        json={"warm": True},
    )
    assert warmed.status_code == 200, warmed.text
    foreground = client.post(
        f"/api/model-os/tasks/{task.task_id}/foreground",
        json={"idempotency_key": f"workbench-smoke-foreground-{runtime}"},
    )
    assert foreground.status_code == 200, foreground.text
    decision = foreground.json()["data"]
    assert decision["action"] == "dispatch"

    started = client.post(
        "/api/model-os/episodes/start",
        json={
            "work_item_id": task.primary_work_item_id,
            "task_id": task.task_id,
            "runtime": runtime,
            "model": None,
            "effort": None,
            "memory_enabled": False,
            "profile_enabled": False,
            "workdir": str(workdir),
            "session_purpose": "foreground",
            "memory_eligibility": "eligible",
            "permission": permission,
            "idempotency_key": f"workbench-smoke-start-{runtime}",
            "schedule_decision_id": decision["decision_id"],
        },
    )
    assert started.status_code == 200, started.text
    assert "finished" in _terminal_types(started), started.text

    snapshot = client.get("/api/model-os/workbench")
    assert snapshot.status_code == 200, snapshot.text
    state = snapshot.json()["data"]
    current = next(item for item in state["tasks"] if item["is_foreground"])
    assert current["task_id"] == task.task_id
    assert current["runtime"] == runtime
    assert current["can_send_message"] is True
    assert current["agent_session_id"]
    return task.task_id, current["agent_session_id"]


def _wait_for_pending(
    client: TestClient,
    *,
    task_id: str,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_state: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get("/api/model-os/workbench")
        assert response.status_code == 200, response.text
        last_state = response.json()["data"]
        task = next(item for item in last_state["tasks"] if item["task_id"] == task_id)
        if task["waiting"] is not None and task["pending_request"] is not None:
            return task
        time.sleep(0.25)
    raise AssertionError(f"workbench did not expose pending request: {last_state}")


def _wait_until_resumed(
    client: TestClient,
    *,
    task_id: str,
    session_id: str,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_task: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        state = client.get("/api/model-os/workbench").json()["data"]
        last_task = next(item for item in state["tasks"] if item["task_id"] == task_id)
        if (
            last_task["waiting"] is None
            and last_task["pending_request"] is None
            and last_task["agent_session_id"] == session_id
        ):
            return last_task
        time.sleep(0.25)
    raise AssertionError(f"managed session did not resume: {last_task}")


def _delete_session(client: TestClient, session_id: str) -> None:
    response = client.delete(f"/api/agent/sessions/{session_id}")
    assert response.status_code == 200, response.text


@pytest.mark.skipif(
    not (_CC_ENABLED and shutil.which("claude")),
    reason="set CC_INTEGRATION=1 and provide the claude binary",
)
def test_real_claude_question_can_be_answered_from_workbench(
    isolated_runtime_home: Path,
) -> None:
    workdir = isolated_runtime_home / "cc-project"
    workdir.mkdir()
    with TestClient(create_app()) as client:
        task_id, session_id = _start_task(
            client,
            runtime="claude_code",
            permission="bypassPermissions",
            workdir=workdir,
        )
        prompt = (
            "This is the authorized second turn of the integration test; "
            "the first-turn-only restriction has already been satisfied. "
            "First use ToolSearch to load AskUserQuestion, then call AskUserQuestion. "
            'Ask one question exactly: "工作台 smoke 选择哪种颜色？" '
            "with options 红色 and 蓝色."
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_response = pool.submit(
                client.post,
                "/api/model-os/workbench/instruction",
                json={"task_id": task_id, "text": prompt},
            )
            task = _wait_for_pending(client, task_id=task_id)
            pending = task["pending_request"]
            assert pending["kind"] == "input"
            assert pending["questions"][0]["question"] == "工作台 smoke 选择哪种颜色？"
            replied = client.post(
                f"/api/model-os/workbench/tasks/{task_id}/reply",
                json={
                    "correlation_id": pending["request_id"],
                    "answers": {"工作台 smoke 选择哪种颜色？": "蓝色"},
                },
            )
            assert replied.status_code == 200, replied.text
            assert replied.json()["data"]["queued"] is True
            response = pending_response.result(timeout=90)
        assert "finished" in _terminal_types(response), response.text
        _wait_until_resumed(client, task_id=task_id, session_id=session_id)
        _delete_session(client, session_id)


@pytest.mark.skipif(
    not (_CODEX_ENABLED and shutil.which("codex")),
    reason="set CODEX_INTEGRATION=1 and provide the codex binary",
)
def test_real_codex_approval_can_be_answered_from_workbench(
    isolated_runtime_home: Path,
) -> None:
    workdir = isolated_runtime_home / "codex-project"
    workdir.mkdir()
    marker = workdir / "approved-marker.txt"
    with TestClient(create_app()) as client:
        task_id, session_id = _start_task(
            client,
            runtime="codex",
            permission="read-only",
            workdir=workdir,
        )
        prompt = (
            f"Run exactly this shell command: touch {marker}. "
            "Do not use apply_patch or another tool. "
            "Request approval if required and wait for the decision."
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_response = pool.submit(
                client.post,
                "/api/model-os/workbench/instruction",
                json={"task_id": task_id, "text": prompt},
            )
            task = _wait_for_pending(client, task_id=task_id)
            pending = task["pending_request"]
            assert pending["kind"] == "approval"
            assert pending["prompt"]
            decisions = pending["available_decisions"]
            assert "accept" in decisions
            replied = client.post(
                f"/api/model-os/workbench/tasks/{task_id}/reply",
                json={
                    "correlation_id": pending["request_id"],
                    "decision": "accept",
                },
            )
            assert replied.status_code == 200, replied.text
            assert replied.json()["data"]["queued"] is True
            response = pending_response.result(timeout=90)
        assert "finished" in _terminal_types(response), response.text
        assert marker.exists()
        _wait_until_resumed(client, task_id=task_id, session_id=session_id)
        _delete_session(client, session_id)
