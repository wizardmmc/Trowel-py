"""验证 discussion HTTP envelope 和事件循环调度边界。"""

from __future__ import annotations

import time
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tests.discussion.support import (
    FakeConfigurationCatalog,
    FakeParticipantSessions,
)
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.routes import router
from trowel_py.discussion.service import DiscussionService


def _client(tmp_path: Path) -> TestClient:
    """创建使用真实 FastAPI 路由和隔离 discussion 后端的客户端。

    Args:
        tmp_path: 当前测试隔离目录。

    Returns:
        已装配 service 和 event bus 的 TestClient。
    """

    db_path = tmp_path / "trowel.db"

    def opener():
        """打开路由测试使用的短连接仓储。"""

        return open_discussion_repository(db_path)

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    events = DiscussionEventBus()
    coordinator = DiscussionCoordinator(
        opener,
        artifacts,
        FakeParticipantSessions(),
        events,
        DiscussionEpisodeWriter(artifacts, memory_root=tmp_path / "memory"),
    )
    service = DiscussionService(
        opener,
        artifacts,
        coordinator,
        events,
        FakeConfigurationCatalog(),
    )
    app = FastAPI()
    app.state.discussion_service = service
    app.state.discussion_events = events
    app.include_router(router, prefix="/api/discussions")
    return TestClient(app)


def _create_payload() -> dict[str, object]:
    """返回 HTTP 测试使用的合法用户参与模式 payload。"""

    return {
        "request_id": "route-create",
        "topic": "路由必须在应用事件循环中启动协调任务",
        "workdir": "/tmp",
        "progression_mode": "user_guided",
        "participants": [
            {
                "name": "glm",
                "session_configuration_id": "cc-glm",
                "permission_mode": "acceptEdits",
                "memory_enabled": True,
                "profile_enabled": False,
                "self_enabled": True,
            },
            {
                "name": "gpt",
                "session_configuration_id": "codex-gpt",
                "permission_preset": "workspace-write",
                "memory_enabled": False,
                "profile_enabled": True,
                "self_enabled": False,
            },
        ],
    }


def test_create_persists_and_returns_participant_context_switches(
    tmp_path: Path,
) -> None:
    """刷新页面后仍能看到每位参与者各自冻结的上下文开关。"""

    with _client(tmp_path) as client:
        response = client.post("/api/discussions", json=_create_payload())
        discussion = response.json()["data"]
        reloaded = client.get(
            f"/api/discussions/{discussion['id']}"
        ).json()["data"]

    assert response.status_code == 200
    assert [
        (
            item["memory_enabled"],
            item["profile_enabled"],
            item["self_enabled"],
        )
        for item in reloaded["participants"]
    ] == [(True, False, True), (False, True, False)]
    assert [
        (item["permission_mode"], item["permission_preset"])
        for item in reloaded["participants"]
    ] == [("acceptEdits", None), (None, "workspace-write")]


def test_create_rejects_permission_from_the_other_runtime(tmp_path: Path) -> None:
    """不能把 Codex permission preset 静默套到 Claude Code 参与者。"""

    payload = _create_payload()
    participants = payload["participants"]
    assert isinstance(participants, list)
    participants[0].pop("permission_mode")
    participants[0]["permission_preset"] = "workspace-write"

    with _client(tmp_path) as client:
        response = client.post("/api/discussions", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DISCUSSION_PERMISSION_INVALID"


def test_start_route_runs_coordinator_on_application_event_loop(
    tmp_path: Path,
) -> None:
    """同步路由线程不得调用 ``asyncio.create_task``；start 必须在应用 loop 中。"""

    with _client(tmp_path) as client:
        created_response = client.post("/api/discussions", json=_create_payload())
        assert created_response.status_code == 200
        created = created_response.json()["data"]
        started_response = client.post(
            f"/api/discussions/{created['id']}/start",
            json={"command_id": "route-start", "expected_version": created["version"]},
        )
        assert started_response.status_code == 200

        deadline = time.monotonic() + 2
        while True:
            snapshot = client.get(f"/api/discussions/{created['id']}").json()["data"]
            if snapshot["status"] == "waiting_user":
                break
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "discussion route did not finish its background round"
                )
            time.sleep(0.01)
        transcript_response = client.get(
            f"/api/discussions/{created['id']}/transcript"
        )

    assert snapshot["rounds"][0]["status"] == "published"
    assert all(
        item["content"] is not None for item in snapshot["rounds"][0]["participants"]
    )
    assert transcript_response.status_code == 200
    assert transcript_response.headers["content-type"].startswith("text/markdown")
    assert "answer-glm-round-1" in transcript_response.text


def test_validation_error_uses_safe_envelope_without_echoing_body(
    tmp_path: Path,
) -> None:
    """Pydantic 细节和研讨正文不得出现在 422 响应中。"""

    payload = _create_payload()
    payload["topic"] = "不能回显的私密议题"
    payload["participants"] = []
    with _client(tmp_path) as client:
        response = client.post("/api/discussions", json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "data": None,
        "error": {
            "code": "DISCUSSION_INVALID_REQUEST",
            "message": "研讨请求字段无效",
        },
    }
    assert "不能回显" not in response.text


def test_missing_artifact_returns_sanitized_error_and_safe_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """公开 GET 不得把 artifact 绝对路径写入响应或普通错误日志。"""

    with _client(tmp_path) as client:
        created = client.post("/api/discussions", json=_create_payload()).json()["data"]
        client.post(
            f"/api/discussions/{created['id']}/start",
            json={"command_id": "start", "expected_version": created["version"]},
        )
        deadline = time.monotonic() + 2
        while True:
            snapshot = client.get(f"/api/discussions/{created['id']}").json()["data"]
            if snapshot["status"] == "waiting_user":
                break
            if time.monotonic() >= deadline:
                raise AssertionError("discussion did not publish")
            time.sleep(0.01)
        private_file = next(
            (tmp_path / "data" / "discussions" / created["id"]).glob(
                "rounds/*/participants/*/attempts/*/final.md"
            )
        )
        private_file.unlink()
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="trowel_py.discussion.routes"):
            response = client.get(f"/api/discussions/{created['id']}")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DISCUSSION_INTERNAL_ERROR"
    combined = response.text + caplog.text
    assert str(tmp_path) not in combined
    assert "final.md" not in combined


class _FailingStreamService:
    """首次边界检查成功，生成 SSE publication 时模拟私有文件缺失。"""

    def __init__(self, private_path: Path) -> None:
        """保存不得泄漏的伪 artifact 绝对路径。"""

        self.private_path = private_path
        self.get_calls = 0

    def get(self, discussion_id: str) -> dict[str, object]:
        """第一次返回快照，第二次抛出带绝对路径的文件错误。"""

        del discussion_id
        self.get_calls += 1
        if self.get_calls == 1:
            return {"rounds": []}
        raise FileNotFoundError(self.private_path)

    def list_events(
        self,
        discussion_id: str,
        *,
        after_sequence: int,
    ) -> tuple[dict[str, object], ...]:
        """返回一条会触发快照读取的发布事件。"""

        del discussion_id
        if after_sequence > 0:
            return ()
        return (
            {
                "sequence": 1,
                "type": "round_published",
                "version": 2,
                "round_number": 1,
                "created_at": "2026-08-06T00:00:00",
            },
        )


def test_sse_generator_sanitizes_artifact_failure_after_response_started(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """StreamingResponse 建立后的异常也只发送固定错误帧和安全日志。"""

    private_path = tmp_path / "data" / "discussions" / "secret" / "final.md"
    app = FastAPI()
    app.state.discussion_service = _FailingStreamService(private_path)
    app.state.discussion_events = DiscussionEventBus()
    app.include_router(router, prefix="/api/discussions")
    with (
        TestClient(app) as client,
        caplog.at_level(
            logging.ERROR,
            logger="trowel_py.discussion.routes",
        ),
    ):
        with client.stream("GET", "/api/discussions/discussion-1/events") as response:
            body = "\n".join(response.iter_lines())

    assert response.status_code == 200
    assert "DISCUSSION_INTERNAL_ERROR" in body
    assert str(tmp_path) not in body + caplog.text
    assert "final.md" not in body + caplog.text
