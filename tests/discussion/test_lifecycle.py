"""验证停止、删除、Episode 保留和未知资源对账。"""

from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from tests.discussion.support import (
    FakeConfigurationCatalog,
    FakeParticipantSessions,
)
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.errors import (
    DiscussionNotFoundError,
    DiscussionRuntimeError,
)
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.schemas import (
    CreateDiscussionRequest,
    StopDiscussionRequest,
    VersionedCommand,
)
from trowel_py.discussion.service import DiscussionService


class UncloseableParticipantSessions(FakeParticipantSessions):
    """模拟 runtime 无法确认 participant 资源已关闭。"""

    async def close(self, agent_session_id: str) -> bool:
        """保留测试 binding 并返回未知关闭结果。

        Args:
            agent_session_id: 当前 participant 会话 ID。

        Returns:
            固定为 False。
        """

        del agent_session_id
        return False


class CloseFailsTwiceSessions(FakeParticipantSessions):
    """两轮资源对账都失败，第三轮才确认关闭。"""

    def __init__(self, *, release: asyncio.Event) -> None:
        """按两 participant 计算四次失败，并保留运行轮屏障。"""

        super().__init__(release=release)
        self.failures_remaining = 4

    async def close(self, agent_session_id: str) -> bool:
        """前四次返回未知，之后删除 binding 并确认关闭。"""

        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            return False
        return await super().close(agent_session_id)


class CloseFailsOnePassSessions(FakeParticipantSessions):
    """stop 阶段两位参与者关闭失败，delete 阶段恢复成功。"""

    def __init__(self) -> None:
        """创建恰好覆盖首轮两次 close 的失败计数。"""

        super().__init__()
        self.failures_remaining = 2

    async def close(self, agent_session_id: str) -> bool:
        """首轮关闭未知，后续调用按正常测试端口收敛。"""

        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            return False
        return await super().close(agent_session_id)


def _system(
    tmp_path: Path,
    sessions: FakeParticipantSessions,
) -> tuple[DiscussionService, object, DiscussionCoordinator]:
    """装配生命周期测试使用的隔离 service 和 repository opener。"""

    db_path = tmp_path / "trowel.db"

    def opener():
        """打开当前测试主库。"""

        return open_discussion_repository(db_path)

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    events = DiscussionEventBus()
    coordinator = DiscussionCoordinator(
        opener,
        artifacts,
        sessions,
        events,
        DiscussionEpisodeWriter(artifacts, memory_root=tmp_path / "memory"),
    )
    return (
        DiscussionService(
            opener,
            artifacts,
            coordinator,
            events,
            FakeConfigurationCatalog(),
        ),
        opener,
        coordinator,
    )


def _request(request_id: str) -> CreateDiscussionRequest:
    """创建无需开始轮次即可测试停止的研讨。"""

    return CreateDiscussionRequest(
        request_id=request_id,
        topic="停止与删除必须收敛资源",
        workdir="/tmp",
        progression_mode="user_guided",
        participants=[
            {"name": "glm", "session_configuration_id": "cc-glm"},
            {"name": "gpt", "session_configuration_id": "codex-gpt"},
        ],
    )


@pytest.mark.anyio
async def test_stop_is_idempotent_and_delete_keeps_episode_provenance(
    tmp_path: Path,
) -> None:
    """重复停止只写一份 Episode，软删除后该 provenance 文件仍存在。"""

    service, opener, _ = _system(tmp_path, FakeParticipantSessions())
    created = await service.create(_request("stop-delete"))
    stop = StopDiscussionRequest(
        command_id="stop",
        expected_version=created["version"],
        reason="用户结束讨论",
    )
    stopped = await service.stop_discussion(created["id"], stop)
    retried = await service.stop_discussion(created["id"], stop)
    episode_files = list((tmp_path / "memory" / "episodes").glob("*.md"))

    assert stopped["status"] == retried["status"] == "stopped"
    assert all(item["status"] == "closed" for item in retried["participants"])
    assert len(episode_files) == 1
    deleted = await service.delete(
        created["id"],
        VersionedCommand(
            command_id="delete",
            expected_version=retried["version"],
        ),
    )

    assert deleted["deleted"] is True
    assert episode_files[0].exists()
    with pytest.raises(DiscussionNotFoundError):
        service.get(created["id"])
    with opener() as repository:
        tombstone = repository.get_discussion(created["id"], include_deleted=True)
    assert tombstone.episode_id == f"discussion-{created['id']}"


@pytest.mark.anyio
async def test_unknown_close_result_blocks_delete_and_keeps_reconcile_state(
    tmp_path: Path,
) -> None:
    """无法确认关闭的 participant 不能随 soft delete 一起被当作已收敛。"""

    service, opener, _ = _system(tmp_path, UncloseableParticipantSessions())
    created = await service.create(_request("unknown-close"))
    stopped = await service.stop_discussion(
        created["id"],
        StopDiscussionRequest(
            command_id="stop",
            expected_version=created["version"],
            reason="测试未知关闭",
        ),
    )
    assert stopped["status"] == "stopped"
    assert all(item["status"] == "needs_reconcile" for item in stopped["participants"])

    with pytest.raises(DiscussionRuntimeError, match="尚未完成对账"):
        await service.delete(
            created["id"],
            VersionedCommand(
                command_id="delete",
                expected_version=stopped["version"],
            ),
        )
    with opener() as repository:
        retained = repository.get_discussion(created["id"])
    assert retained.status == "stopped"


@pytest.mark.anyio
async def test_delete_waits_until_stopped_episode_outbox_is_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Episode 写失败后 stop 可重试，delete 不能先删掉唯一补偿入口。"""

    service, opener, coordinator = _system(tmp_path, FakeParticipantSessions())
    created = await service.create(_request("episode-outbox"))

    def fail_episode(*args, **kwargs):
        """模拟 Memory Episode 文件系统持续不可写。"""

        del args, kwargs
        raise OSError("injected episode failure")

    monkeypatch.setattr(coordinator._episode_writer, "write", fail_episode)
    stop = StopDiscussionRequest(
        command_id="stop",
        expected_version=created["version"],
        reason="测试 Episode outbox",
    )
    with pytest.raises(DiscussionRuntimeError, match="Episode"):
        await service.stop_discussion(created["id"], stop)
    with opener() as repository:
        stopped = repository.get_discussion(created["id"])
    assert stopped.status == "stopped"
    assert stopped.episode_id is None

    with pytest.raises(DiscussionRuntimeError, match="Episode"):
        await service.delete(
            created["id"],
            VersionedCommand(
                command_id="delete",
                expected_version=stopped.version,
            ),
        )
    with opener() as repository:
        retained = repository.get_discussion(created["id"])
    assert retained.status == "stopped"


@pytest.mark.anyio
async def test_delete_uses_post_reconciliation_version_in_same_command(
    tmp_path: Path,
) -> None:
    """delete 本次补关资源并递增内部版本后，无需用户再重试一次。"""

    service, _, _ = _system(tmp_path, CloseFailsOnePassSessions())
    created = await service.create(_request("delete-after-close"))
    stopped = await service.stop_discussion(
        created["id"],
        StopDiscussionRequest(
            command_id="stop",
            expected_version=created["version"],
            reason="先留下资源 outbox",
        ),
    )

    deleted = await service.delete(
        created["id"],
        VersionedCommand(
            command_id="delete",
            expected_version=stopped["version"],
        ),
    )

    assert deleted["deleted"] is True
    assert deleted["version"] > stopped["version"]


@pytest.mark.anyio
async def test_startup_retries_terminal_participant_resource_outbox(
    tmp_path: Path,
) -> None:
    """stopped 下关闭失败的 participant 会在下一次应用启动继续核验到 closed。"""

    first_service, opener, _ = _system(tmp_path, UncloseableParticipantSessions())
    created = await first_service.create(_request("terminal-resource-outbox"))
    stopped = await first_service.stop_discussion(
        created["id"],
        StopDiscussionRequest(
            command_id="stop",
            expected_version=created["version"],
            reason="测试重启补关",
        ),
    )
    assert all(item["status"] == "needs_reconcile" for item in stopped["participants"])

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    sessions = FakeParticipantSessions()
    second = DiscussionCoordinator(
        opener,
        artifacts,
        sessions,
        DiscussionEventBus(),
        DiscussionEpisodeWriter(artifacts, memory_root=tmp_path / "memory"),
    )
    await second.start()

    with opener() as repository:
        recovered = repository.get_discussion(created["id"])
        pending = repository.list_terminal_resource_reconcile_discussion_ids()
    assert all(item.status == "closed" for item in recovered.participants)
    assert pending == ()


@pytest.mark.anyio
async def test_user_stop_records_cancelled_only_after_resources_close(
    tmp_path: Path,
) -> None:
    """用户 stop 不是 runtime interrupted ACK；关闭核验后写独立 cancelled 状态。"""

    release = asyncio.Event()
    sessions = FakeParticipantSessions(release=release)
    service, opener, coordinator = _system(tmp_path, sessions)
    created = await service.create(_request("stop-running"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(sessions.started.wait(), timeout=1)
    running = service.get(created["id"])

    stopped = await service.stop_discussion(
        created["id"],
        StopDiscussionRequest(
            command_id="stop",
            expected_version=running["version"],
            reason="用户主动停止",
        ),
    )
    await coordinator.stop()

    with opener() as repository:
        statuses = {
            str(row["status"])
            for row in repository.connection.execute(
                "SELECT status FROM discussion_attempts"
            ).fetchall()
        }
    assert stopped["status"] == "stopped"
    assert statuses == {"cancelled"}
    assert "interrupted" not in statuses


@pytest.mark.anyio
async def test_stopped_episode_waits_for_successful_resource_reconciliation(
    tmp_path: Path,
) -> None:
    """stop 与首次重启都关不掉资源时，Episode 不能冻结瞬时 STOP_REQUESTED。"""

    release = asyncio.Event()
    sessions = CloseFailsTwiceSessions(release=release)
    service, opener, coordinator = _system(tmp_path, sessions)
    created = await service.create(_request("episode-after-close"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(sessions.started.wait(), timeout=1)
    running = service.get(created["id"])
    stopped = await service.stop_discussion(
        created["id"],
        StopDiscussionRequest(
            command_id="stop",
            expected_version=running["version"],
            reason="验证 Episode 时序",
        ),
    )
    assert stopped["status"] == "stopped"
    with opener() as repository:
        assert repository.get_discussion(created["id"]).episode_id is None

    await coordinator.start()
    with opener() as repository:
        after_failed_restart = repository.get_discussion(created["id"])
    assert after_failed_restart.episode_id is None

    await coordinator.start()
    with opener() as repository:
        recovered = repository.get_discussion(created["id"])
        events = repository.list_events(created["id"], after_sequence=0)
    episode = next((tmp_path / "memory" / "episodes").glob("*.md")).read_text(
        encoding="utf-8"
    )
    assert recovered.episode_id == f"discussion-{created['id']}"
    assert all(item.status == "closed" for item in recovered.participants)
    assert "cancelled" in episode
    assert any(item["type"] == "resources_reconciled" for item in events)
    assert recovered.version > stopped["version"]
