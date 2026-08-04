"""验证 Agent Host 持有交互委派句柄的内部 HTTP 边界。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def _child_body(parent_id: str, workdir: Path) -> dict[str, Any]:
    """构造符合父会话继承和非递归约束的交互 child 参数。"""

    return {
        "runtime": "claude_code",
        "workdir": str(workdir.resolve()),
        "memory_enabled": True,
        "profile_enabled": True,
        "self_enabled": True,
        "session_kind": "delegate",
        "memory_eligibility": False,
        "agent_mcp_enabled": False,
        "parent_session_id": parent_id,
        "delegation_depth": 1,
        "permission_mode": "bypassPermissions",
    }


class _Broker:
    """记录内部路由调用，模拟跨 MCP 进程共享的应用级 broker。"""

    def __init__(self, owner: str = "parent-1") -> None:
        self.owner = owner
        self.closed_parents: list[str] = []

    async def start(
        self,
        *,
        parent_session_id: str,
        task: str,
        create_body: dict[str, Any],
    ) -> dict[str, Any]:
        """返回固定初始句柄，并核对路由透传的创建事实。"""

        assert parent_session_id == self.owner
        assert task == "long work"
        assert create_body["runtime"] == "claude_code"
        assert create_body["session_kind"] == "delegate"
        assert create_body["agent_mcp_enabled"] is False
        assert create_body["parent_session_id"] == self.owner
        return {"delegation_id": "delegation-1", "status": "starting", "version": 0}

    def parent_session_id(self, delegation_id: str) -> str:
        """返回固定句柄的父会话。"""

        assert delegation_id == "delegation-1"
        return self.owner

    def status(self, delegation_id: str) -> dict[str, Any]:
        """返回固定完成快照。"""

        assert delegation_id == "delegation-1"
        return {"delegation_id": delegation_id, "status": "completed", "version": 2}

    async def respond(
        self, delegation_id: str, answers: dict[str, str]
    ) -> dict[str, Any]:
        """返回已经接受答案的快照。"""

        assert delegation_id == "delegation-1"
        assert answers == {"Question?": "Answer"}
        return {"delegation_id": delegation_id, "status": "running", "version": 3}

    async def close(self, delegation_id: str) -> dict[str, Any]:
        """返回句柄已经关闭的快照。"""

        assert delegation_id == "delegation-1"
        return {"delegation_id": delegation_id, "status": "closed", "version": 4}

    async def close_parent(self, parent_session_id: str) -> None:
        """记录父会话关闭前的收敛请求。"""

        self.closed_parents.append(parent_session_id)


class _Wakeup:
    """记录父会话删除时对通知 worker 的收敛。"""

    def __init__(self) -> None:
        self.closed_parents: list[str] = []
        self.forgotten_delegations: list[str] = []

    async def close_parent(self, parent_session_id: str) -> None:
        """记录要停止投递的父会话。"""

        self.closed_parents.append(parent_session_id)

    async def forget_delegation(self, delegation_id: str) -> None:
        """记录显式关闭后释放的委派去重状态。"""

        self.forgotten_delegations.append(delegation_id)


def test_internal_delegation_routes_share_application_broker(
    client: TestClient,
    workdir: Path,
) -> None:
    """不同 HTTP 请求通过同一应用级 broker 读取并推进句柄。"""

    created = client.post(
        "/api/agent/sessions",
        json={
            "runtime": "claude_code",
            "workdir": str(workdir),
            "permission_mode": "bypassPermissions",
        },
    )
    parent_id = created.json()["data"]["session_id"]
    broker = _Broker(parent_id)
    wakeup = _Wakeup()
    client.app.state.agent_delegation_broker = broker
    client.app.state.agent_delegation_wakeup = wakeup

    started = client.post(
        "/api/agent/internal/delegations",
        json={
            "parent_session_id": parent_id,
            "task": "long work",
            "create_body": _child_body(parent_id, workdir),
        },
    )
    status = client.get(
        "/api/agent/internal/delegations/delegation-1",
        params={"parent_session_id": parent_id},
    )
    answered = client.post(
        "/api/agent/internal/delegations/delegation-1/answers",
        json={
            "parent_session_id": parent_id,
            "answers": {"Question?": "Answer"},
        },
    )
    closed = client.delete(
        "/api/agent/internal/delegations/delegation-1",
        params={"parent_session_id": parent_id},
    )

    assert started.json()["data"]["status"] == "starting"
    assert status.json()["data"]["status"] == "completed"
    assert answered.json()["data"]["status"] == "running"
    assert closed.json()["data"]["status"] == "closed"
    assert wakeup.forgotten_delegations == ["delegation-1"]


def test_internal_delegation_rejects_deleted_parent(client: TestClient) -> None:
    """Host 不登记已经失去父 binding 的后台 child。"""

    client.app.state.agent_delegation_broker = _Broker("missing-parent")

    response = client.post(
        "/api/agent/internal/delegations",
        json={
            "parent_session_id": "missing-parent",
            "task": "long work",
            "create_body": {
                "runtime": "claude_code",
                "parent_session_id": "missing-parent",
            },
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "delegation parent not found"


def test_internal_delegation_rejects_child_policy_override(
    client: TestClient,
    workdir: Path,
) -> None:
    """Host 不信任 MCP 进程传来的目录、身份和递归开关。"""

    created = client.post(
        "/api/agent/sessions",
        json={
            "runtime": "claude_code",
            "workdir": str(workdir),
            "permission_mode": "bypassPermissions",
        },
    )
    parent_id = created.json()["data"]["session_id"]
    client.app.state.agent_delegation_broker = _Broker(parent_id)
    unsafe = _child_body(parent_id, workdir)
    unsafe["agent_mcp_enabled"] = True

    response = client.post(
        "/api/agent/internal/delegations",
        json={
            "parent_session_id": parent_id,
            "task": "long work",
            "create_body": unsafe,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "delegation child configuration does not match parent policy"
    )


def test_internal_delegation_rechecks_parent_permission(
    client: TestClient,
    workdir: Path,
) -> None:
    """父会话不再是全权限时，内部 start 不能只凭旧 MCP 快照继续。"""

    created = client.post(
        "/api/agent/sessions",
        json={
            "runtime": "claude_code",
            "workdir": str(workdir),
            "permission_mode": "default",
        },
    )
    parent_id = created.json()["data"]["session_id"]
    client.app.state.agent_delegation_broker = _Broker(parent_id)

    response = client.post(
        "/api/agent/internal/delegations",
        json={
            "parent_session_id": parent_id,
            "task": "long work",
            "create_body": _child_body(parent_id, workdir),
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "parent permission no longer allows full-access delegation"
    )


def test_internal_delegation_hides_handle_from_another_parent(
    client: TestClient,
) -> None:
    """错误父会话不能通过内部状态接口确认句柄是否存在。"""

    client.app.state.agent_delegation_broker = _Broker()

    response = client.get(
        "/api/agent/internal/delegations/delegation-1",
        params={"parent_session_id": "parent-2"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "interactive delegation not found"


def test_parent_delete_closes_its_interactive_delegations(
    client: TestClient,
) -> None:
    """删除父会话前先要求应用级 broker 收敛它持有的 child。"""

    broker = _Broker()
    wakeup = _Wakeup()
    client.app.state.agent_delegation_broker = broker
    client.app.state.agent_delegation_wakeup = wakeup

    response = client.delete("/api/agent/sessions/missing-parent")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "not_found"
    assert broker.closed_parents == ["missing-parent"]
    assert wakeup.closed_parents == ["missing-parent"]
