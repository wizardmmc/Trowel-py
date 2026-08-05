from dataclasses import replace
from collections.abc import Callable
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.capacity import CapacityLimits
from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.lifecycle import SessionCloseResult

from tests.agent_host.routes.support import (
    cc_payload,
    codex_payload,
    create_session,
)


def test_post_sessions_creates_cc(client: TestClient, workdir: Path) -> None:
    response = client.post("/api/agent/sessions", json=cc_payload(workdir))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["runtime"] == "claude_code"
    assert data["session_id"]


def test_post_sessions_creates_codex(client: TestClient, workdir: Path) -> None:
    response = client.post("/api/agent/sessions", json=codex_payload(workdir))

    assert response.status_code == 200
    assert response.json()["data"]["runtime"] == "codex"


def test_post_sessions_reuses_same_idempotent_create(
    client: TestClient, hub: SessionHub, workdir: Path
) -> None:
    """renderer 未确认首次响应时，用同一请求 ID 重试不会创建幽灵会话。"""

    headers = {"X-Trowel-Request-Id": "create-request-1"}
    first = client.post(
        "/api/agent/sessions", json=cc_payload(workdir), headers=headers
    )
    second = client.post(
        "/api/agent/sessions", json=cc_payload(workdir), headers=headers
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["session_id"] == second.json()["data"]["session_id"]
    assert len(hub.store.list_all()) == 1


def test_post_sessions_rejects_idempotency_key_reuse_with_other_params(
    client: TestClient, workdir: Path
) -> None:
    """同一创建请求 ID 不能绑定两套参数。"""

    headers = {"X-Trowel-Request-Id": "create-request-2"}
    first = client.post(
        "/api/agent/sessions", json=cc_payload(workdir), headers=headers
    )
    second = client.post(
        "/api/agent/sessions",
        json=cc_payload(workdir, memory_enabled=False),
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 400


def test_post_sessions_missing_workdir_400(
    client: TestClient,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    response = client.post(
        "/api/agent/sessions",
        json=cc_payload(missing),
    )

    assert response.status_code == 400


def test_post_sessions_invalid_runtime_422(
    client: TestClient,
    workdir: Path,
) -> None:
    response = client.post(
        "/api/agent/sessions",
        json={"runtime": "gemini", "workdir": str(workdir)},
    )

    assert response.status_code == 422


def test_delegate_connection_capacity_returns_stable_409(
    hub_factory: Callable[[CapacityLimits | None], SessionHub],
    client_factory: Callable[[SessionHub], TestClient],
    workdir: Path,
) -> None:
    hub = hub_factory(
        CapacityLimits(
            user_connections=20,
            delegate_connections=1,
            delegate_running=5,
        )
    )
    with client_factory(hub) as client:
        create_session(
            client,
            cc_payload(workdir, session_kind="delegate"),
        )
        response = client.post(
            "/api/agent/sessions",
            json=codex_payload(workdir, session_kind="delegate"),
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "当前委派数量已满：连接上限为 1"
    }


def test_twenty_session_runtime_pressure_gate(
    client: TestClient, hub: SessionHub, workdir: Path
) -> None:
    """进程内 ASGI 边界在 20 连接/5 在跑时仍可读、可中断并可关闭。"""

    sessions = [create_session(client, cc_payload(workdir)) for _ in range(20)]

    async def hold_turn(_text: str):
        """保持可控 runtime 在跑，直到 close 取消 detached owner。"""

        yield {"type": "turn_start", "turn_id": "held-turn"}
        await asyncio.Event().wait()

    for session in sessions[:5]:
        hub._cc_registry[session["session_id"]].send = hold_turn
        response = client.post(
            f"/api/agent/sessions/{session['session_id']}/turns",
            json={"text": "hold"},
        )
        assert response.status_code == 200

    connection_overflow = client.post(
        "/api/agent/sessions", json=cc_payload(workdir)
    )
    running_overflow = client.post(
        f"/api/agent/sessions/{sessions[5]['session_id']}/turns",
        json={"text": "sixth"},
    )
    assert connection_overflow.status_code == 409
    assert running_overflow.status_code == 409

    for session in sessions:
        assert client.get("/api/agent/session-defaults").status_code == 200
        assert client.get(
            f"/api/agent/sessions/{session['session_id']}/history"
        ).status_code == 200
    for session in sessions[:5]:
        assert client.post(
            f"/api/agent/sessions/{session['session_id']}/interrupt"
        ).status_code == 200
    for session in sessions:
        assert client.delete(
            f"/api/agent/sessions/{session['session_id']}"
        ).status_code == 200

    assert hub.list_active()[0] == []


def test_get_active_lists_mixed(
    client: TestClient,
    workdir: Path,
) -> None:
    create_session(client, cc_payload(workdir))
    create_session(client, codex_payload(workdir))

    response = client.get("/api/agent/sessions/active")
    assert response.status_code == 200
    sessions = response.json()["data"]["sessions"]
    assert {session["runtime"] for session in sessions} == {
        "claude_code",
        "codex",
    }


def test_get_active_excludes_delegates_but_id_routes_keep_working(
    client: TestClient,
    workdir: Path,
) -> None:
    user = create_session(client, cc_payload(workdir))
    cc_delegate = create_session(
        client,
        cc_payload(workdir, session_kind="delegate"),
    )
    codex_delegate = create_session(
        client,
        codex_payload(workdir, session_kind="delegate"),
    )

    active = client.get("/api/agent/sessions/active").json()["data"]

    assert [session["session_id"] for session in active["sessions"]] == [
        user["session_id"]
    ]
    assert active["active_id"] == user["session_id"]
    for delegate in (cc_delegate, codex_delegate):
        session_id = delegate["session_id"]
        assert client.get(f"/api/agent/sessions/{session_id}").status_code == 200
        assert (
            client.post(f"/api/agent/sessions/{session_id}/interrupt").status_code
            == 200
        )
        assert client.delete(f"/api/agent/sessions/{session_id}").status_code == 200

    assert (
        client.get("/api/agent/sessions/active").json()["data"]["active_id"]
        == user["session_id"]
    )


def test_activate_delegate_is_rejected(
    client: TestClient,
    workdir: Path,
) -> None:
    delegate = create_session(
        client,
        codex_payload(workdir, session_kind="delegate"),
    )

    response = client.post(
        f"/api/agent/sessions/{delegate['session_id']}/activate"
    )

    assert response.status_code == 422


def test_get_session_defaults_returns_latest_used_runtime_config(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    created = create_session(
        client,
        codex_payload(
            workdir,
            model="gpt-5.6-sol",
            effort="ultra",
            permission_preset="workspace-write",
            memory_enabled=False,
            profile_enabled=True,
        ),
    )
    hub._store.update_native(
        created["session_id"], native_session_id="native-used-thread"
    )

    response = client.get("/api/agent/session-defaults")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "runtime": "codex",
        "model": "gpt-5.6-sol",
        "effort": "ultra",
        "permission_mode": "",
        "permission_preset": "workspace-write",
        "memory_enabled": False,
        "profile_enabled": True,
    }


def test_session_defaults_include_latest_successful_unsent_session(
    client: TestClient,
    workdir: Path,
) -> None:
    create_session(
        client,
        cc_payload(
            workdir,
            model="opus",
            effort="max",
            permission_mode="acceptEdits",
            memory_enabled=True,
            profile_enabled=False,
        ),
    )

    response = client.get("/api/agent/session-defaults")

    assert response.json()["data"] == {
        "runtime": "claude_code",
        "model": "opus",
        "effort": "max",
        "permission_mode": "acceptEdits",
        "memory_enabled": True,
        "profile_enabled": False,
    }


def test_session_defaults_legacy_timestamp_tie_uses_write_order(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    common = {
        "native_session_id": None,
        "workdir": str(workdir),
        "effort": "max",
        "memory_enabled": True,
        "profile_enabled": True,
        "capabilities": ("tools",),
        "name": "project",
    }
    first = make_binding(
        session_id="legacy-first",
        runtime=Runtime.CLAUDE_CODE,
        model="opus",
        permission="default",
        **common,
    )
    second = make_binding(
        session_id="legacy-second",
        runtime=Runtime.CODEX,
        model="gpt-5.6-sol",
        permission=None,
        permission_preset="workspace-write",
        **common,
    )
    tied = "2026-07-24T12:00:00"
    hub.store.put(replace(first, created_at=tied, updated_at=tied))
    hub.store.put(replace(second, created_at=tied, updated_at=tied))

    response = client.get("/api/agent/session-defaults")

    assert response.json()["data"]["runtime"] == "codex"


def test_resume_codex_returns_native_effective_config_before_first_message(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    hub.store.put(
        make_binding(
            session_id="old-codex",
            runtime=Runtime.CODEX,
            native_session_id="thread-resume-config",
            workdir=str(workdir),
            model="gpt-5.6-luna",
            effort="medium",
            permission="Workspace write · on-request",
            permission_preset="workspace-write",
            memory_enabled=False,
            profile_enabled=True,
            capabilities=("tools",),
            name="project",
        )
    )
    hub._codex.attach_results["thread-resume-config"] = {
        "thread": {"id": "thread-resume-config"},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": str(workdir),
        "reasoningEffort": "high",
        "serviceTier": None,
        "sandbox": {"type": "dangerFullAccess"},
        "approvalPolicy": "never",
    }

    response = client.post(
        "/api/agent/sessions",
        json={
            "runtime": "codex",
            "workdir": str(workdir),
            "resume_from": "thread-resume-config",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["model"] == "gpt-5.6-sol"
    assert data["effort"] == "high"
    assert data["permission"] == "Full access · never"
    assert data["effective_sandbox"] == "danger-full-access"
    assert data["memory_enabled"] is False
    session = hub._codex.get_session(data["session_id"])
    assert session.next_turn_settings() == (None, None)


def test_post_activate_sets_active(
    client: TestClient,
    workdir: Path,
) -> None:
    first = create_session(client, cc_payload(workdir))
    second = create_session(client, codex_payload(workdir))

    response = client.post(f"/api/agent/sessions/{first['session_id']}/activate")
    assert response.status_code == 200
    assert response.json()["data"]["active_id"] == first["session_id"]
    active = client.get("/api/agent/sessions/active").json()["data"]
    assert active["active_id"] == first["session_id"]
    # 切换活动会话不能销毁其他会话。
    assert second["session_id"] in {
        session["session_id"] for session in active["sessions"]
    }


def test_get_session(client: TestClient, workdir: Path) -> None:
    created = create_session(client, cc_payload(workdir))
    response = client.get(f"/api/agent/sessions/{created['session_id']}")
    assert response.status_code == 200
    assert response.json()["data"]["session_id"] == created["session_id"]


def test_get_session_404(client: TestClient) -> None:
    response = client.get("/api/agent/sessions/unknown")

    assert response.status_code == 404


def test_delete_session(client: TestClient, workdir: Path) -> None:
    created = create_session(client, cc_payload(workdir))
    response = client.delete(f"/api/agent/sessions/{created['session_id']}")
    assert response.status_code == 200
    assert response.json()["data"]["closed"] is True
    assert response.json()["data"]["status"] == "closed"
    assert response.json()["data"]["remaining_resource_count"] == 0
    assert client.get(f"/api/agent/sessions/{created['session_id']}").status_code == 404


def test_delete_session_returns_reconcile_as_data(
    client: TestClient,
    hub: SessionHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """资源未归零是可重试业务结果，不应破坏成功响应 envelope。"""

    async def needs_reconcile(_session_id: str) -> SessionCloseResult:
        """返回固定的未收敛结果。"""

        return SessionCloseResult(
            status="needs_reconcile",
            remaining_resource_count=1,
            remaining_resource_kinds=("codex_session_close",),
            error="Codex close needs reconciliation",
        )

    monkeypatch.setattr(hub, "close_result", needs_reconcile)

    response = client.delete("/api/agent/sessions/session-needs-reconcile")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "closed": False,
            "status": "needs_reconcile",
            "remaining_resource_count": 1,
            "remaining_resource_kinds": ["codex_session_close"],
            "error": "Codex close needs reconciliation",
        },
        "error": None,
    }
