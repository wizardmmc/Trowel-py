from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from tests.agent_host.hub._support import FakeCodexManager
from tests.agent_host.routes.support import (
    cc_payload,
    codex_payload,
    create_session,
    parse_sse,
)
from trowel_py.agent_host.hub import SessionHub
from trowel_py.codex_host.errors import ProtocolViolationError


def _manager(hub: SessionHub) -> FakeCodexManager:
    manager = hub._codex
    assert isinstance(manager, FakeCodexManager)
    return manager


def test_codex_command_roster_is_session_scoped(
    client: TestClient, workdir: Path
) -> None:
    codex = create_session(client, codex_payload(workdir))
    cc = create_session(client, cc_payload(workdir))

    response = client.get(f"/api/agent/sessions/{codex['session_id']}/commands")
    rejected = client.get(f"/api/agent/sessions/{cc['session_id']}/commands")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["data"]["commands"]] == [
        "status",
        "compact",
        "review",
        "goal",
        "diff",
        "agent",
    ]
    assert rejected.status_code == 422


def test_codex_skill_roster_uses_session_connection_and_workdir(
    client: TestClient, workdir: Path
) -> None:
    """技能查询只能使用已保存的 Codex 会话身份与工作目录。"""

    codex = create_session(client, codex_payload(workdir))
    cc = create_session(client, cc_payload(workdir))

    response = client.get(f"/api/agent/sessions/{codex['session_id']}/skills")
    rejected = client.get(f"/api/agent/sessions/{cc['session_id']}/skills")

    assert response.status_code == 200
    catalog = response.json()["data"]
    assert catalog["errors"] == []
    assert catalog["skills"][0] == {
        "name": "development-slice-workflow",
        "description": f"{codex['session_id']} @ {workdir}",
        "scope": "user",
        "enabled": True,
    }
    assert rejected.status_code == 422


def test_codex_skill_roster_top_level_error_does_not_expose_native_path(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原生请求整体失败时，HTTP 502 也不能回显 error message 内嵌路径。"""

    sid = create_session(client, codex_payload(workdir))["session_id"]
    canary = "/private/users/example/.agents/skills/broken/SKILL.md"

    async def fail_skills(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """模拟 app-server 顶层错误把绝对路径拼进 message。"""

        raise ProtocolViolationError(
            f"skills/list failed at {canary}",
            payload={"error": {"message": canary}},
        )

    monkeypatch.setattr(_manager(hub), "list_skills", fail_skills)

    response = client.get(f"/api/agent/sessions/{sid}/skills")

    assert response.status_code == 502
    assert response.json()["detail"] == "codex skill roster failed"
    assert canary not in response.text


def test_compact_and_review_call_native_manager_without_sending_message(
    client: TestClient, hub: SessionHub, workdir: Path
) -> None:
    created = create_session(client, codex_payload(workdir))
    sid = created["session_id"]

    compact = client.post(f"/api/agent/sessions/{sid}/commands/compact")
    review = client.post(
        f"/api/agent/sessions/{sid}/commands/review",
        json={"target": {"type": "baseBranch", "branch": "main"}},
    )

    assert compact.status_code == 200
    assert compact.json()["data"] == {"started": True}
    assert review.status_code == 200
    assert review.json()["data"] == {
        "review_thread_id": "thread-1",
        "turn_id": "review-turn-1",
    }
    manager = _manager(hub)
    assert manager.compactions == [sid]
    assert manager.reviews == [
        {
            "session_id": sid,
            "target": {"type": "baseBranch", "branch": "main"},
        }
    ]
    assert manager.sent == []


def test_review_target_validation_matches_generated_schema(
    client: TestClient, workdir: Path
) -> None:
    sid = create_session(client, codex_payload(workdir))["session_id"]
    url = f"/api/agent/sessions/{sid}/commands/review"

    assert (
        client.post(url, json={"target": {"type": "uncommittedChanges"}}).status_code
        == 200
    )
    assert client.post(url, json={"target": {"type": "baseBranch"}}).status_code == 422
    assert (
        client.post(url, json={"target": {"type": "commit", "sha": ""}}).status_code
        == 422
    )
    assert client.post(url, json={"target": {"type": "custom"}}).status_code == 422
    assert client.post(url, json={"target": {"type": "unknown"}}).status_code == 422


def test_reserved_codex_commands_cannot_bypass_local_dispatch(
    client: TestClient, hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = create_session(client, codex_payload(workdir))["session_id"]
    manager = _manager(hub)

    reserved = client.post(
        f"/api/agent/sessions/{sid}/turns",
        json={"text": "/review focus on auth"},
    )
    ordinary = client.post(
        f"/api/agent/sessions/{sid}/turns",
        json={"text": "/reviewer"},
    )

    async def reject_runtime_call(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("reserved command reached Codex runtime")

    monkeypatch.setattr(manager, "send", reject_runtime_call)
    legacy = client.post(
        f"/api/agent/sessions/{sid}/messages",
        json={"text": "/compact"},
    )

    assert reserved.status_code == 422
    assert "local command" in reserved.json()["detail"]
    assert ordinary.status_code == 200
    legacy_events = parse_sse(legacy.content)
    assert [event["type"] for event in legacy_events] == ["error"]
    assert "local command" in str(legacy_events[0]["payload"]["errors"])
    assert manager.sent == [(sid, "/reviewer")]
