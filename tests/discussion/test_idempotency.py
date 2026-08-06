"""验证 discussion 写命令在并发重试下只提交一次。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from tests.discussion.support import (
    FakeConfigurationCatalog,
    FakeParticipantSessions,
)
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.errors import DiscussionCommandConflictError
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.schemas import (
    AddDiscussionMessageRequest,
    CreateDiscussionRequest,
    VersionedCommand,
)
from trowel_py.discussion.service import DiscussionService


def _system(
    tmp_path: Path,
) -> tuple[DiscussionService, DiscussionCoordinator, object]:
    """装配并发幂等测试使用的隔离后端。"""

    db_path = tmp_path / "trowel.db"

    def opener():
        """打开当前测试主库。"""

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
    return service, coordinator, opener


def _request() -> CreateDiscussionRequest:
    """返回并发调用共用的规范化创建请求。"""

    return CreateDiscussionRequest(
        request_id="same-create",
        topic="同一个请求只能创建一场研讨",
        workdir="/tmp",
        progression_mode="user_guided",
        participants=[
            {"name": "glm", "session_configuration_id": "cc-glm"},
            {"name": "gpt", "session_configuration_id": "codex-gpt"},
        ],
    )


@pytest.mark.anyio
async def test_concurrent_create_and_message_retries_have_one_durable_effect(
    tmp_path: Path,
) -> None:
    """响应丢失造成的并发重试不能产生双 discussion、双消息或不同 ID。"""

    service, coordinator, opener = _system(tmp_path)
    first, second = await asyncio.gather(
        service.create(_request()),
        service.create(_request()),
    )
    assert first["id"] == second["id"]
    service.start(
        first["id"],
        VersionedCommand(command_id="start", expected_version=first["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(first["id"]), timeout=2)
    waiting = service.get(first["id"])
    command = AddDiscussionMessageRequest(
        command_id="same-message",
        expected_version=waiting["version"],
        body="这条补充只保存一次",
    )
    await asyncio.gather(
        asyncio.to_thread(service.add_message, first["id"], command),
        asyncio.to_thread(service.add_message, first["id"], command),
    )

    with opener() as repository:
        discussion_count = repository.connection.execute(
            "SELECT COUNT(*) AS total FROM discussions"
        ).fetchone()["total"]
        message_count = repository.connection.execute(
            "SELECT COUNT(*) AS total FROM discussion_user_messages"
        ).fetchone()["total"]
        command_count = repository.connection.execute(
            "SELECT COUNT(*) AS total FROM discussion_commands WHERE command_type='message'"
        ).fetchone()["total"]

    assert discussion_count == 1
    assert message_count == 2
    assert command_count == 1


@pytest.mark.anyio
async def test_concurrent_conflicting_message_payload_keeps_winner_artifact_consistent(
    tmp_path: Path,
) -> None:
    """同 command ID 的不同正文并发时，SQLite 赢家必须仍指向自己的不可变文件。"""

    service, coordinator, opener = _system(tmp_path)
    created = await service.create(_request())
    service.start(
        created["id"],
        VersionedCommand(
            command_id="start-conflict", expected_version=created["version"]
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    waiting = service.get(created["id"])
    requests = [
        AddDiscussionMessageRequest(
            command_id="conflicting-message",
            expected_version=waiting["version"],
            body=body,
        )
        for body in ("正文甲", "正文乙")
    ]

    outcomes = await asyncio.gather(
        *(
            asyncio.to_thread(service.add_message, created["id"], request)
            for request in requests
        ),
        return_exceptions=True,
    )

    assert (
        sum(isinstance(item, DiscussionCommandConflictError) for item in outcomes) == 1
    )
    with opener() as repository:
        stored = repository.get_discussion(created["id"])
    winner = stored.messages[-1]
    body = DiscussionArtifactStore(tmp_path / "data").read_user_message_body(winner)
    assert body == winner.body
    assert body in {"正文甲", "正文乙"}


@pytest.mark.anyio
async def test_message_artifact_rejects_routing_metadata_drift(tmp_path: Path) -> None:
    """after_round 与 target 决定提示词路由，SQLite 漂移必须被身份校验拒绝。"""

    service, _, opener = _system(tmp_path)
    created = await service.create(_request())
    with opener() as repository:
        discussion = repository.get_discussion(created["id"])
    message = discussion.messages[0]
    artifacts = DiscussionArtifactStore(tmp_path / "data")

    with pytest.raises(ValueError, match="identity mismatch"):
        artifacts.read_user_message_body(replace(message, after_round_number=1))
    with pytest.raises(ValueError, match="identity mismatch"):
        artifacts.read_user_message_body(
            replace(
                message,
                target_scope="participant",
                target_participant_id=discussion.participants[0].id,
            )
        )
