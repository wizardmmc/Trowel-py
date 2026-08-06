"""验证 discussion 的 Episode、Note 与 Profile 三条资格彼此独立。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.discussion.support import (
    FakeConfigurationCatalog,
    FakeParticipantSessions,
)
from tests.profile.distill.support import FINISHED, FakeHost
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.schemas import (
    AddDiscussionMessageRequest,
    CreateDiscussionRequest,
    VersionedCommand,
)
from trowel_py.discussion.service import DiscussionService
from trowel_py.profile.distill import run_daily_distill
from trowel_py.profile.suggestions import load_suggestions


def _build_system(
    tmp_path: Path,
) -> tuple[
    DiscussionService,
    DiscussionCoordinator,
    object,
    DiscussionArtifactStore,
]:
    """装配带独立 Memory 根的 discussion 测试系统。

    Args:
        tmp_path: 当前测试隔离目录。

    Returns:
        service、coordinator、仓储工厂和 artifact 存储。
    """

    db_path = tmp_path / "trowel.db"

    def opener():
        """打开当前测试主库的短连接仓储。"""

        return open_discussion_repository(db_path)

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    events = DiscussionEventBus()
    coordinator = DiscussionCoordinator(
        opener,
        artifacts,
        FakeParticipantSessions(),
        events,
        DiscussionEpisodeWriter(
            artifacts,
            memory_root=tmp_path / "memory",
        ),
    )
    service = DiscussionService(
        opener,
        artifacts,
        coordinator,
        events,
        FakeConfigurationCatalog(),
    )
    return service, coordinator, opener, artifacts


def _request(request_id: str) -> CreateDiscussionRequest:
    """创建两 participant、逐轮用户参与的最小研讨。"""

    return CreateDiscussionRequest(
        request_id=request_id,
        topic="用户坚持真实运行比只看单测更可信",
        workdir="/tmp",
        progression_mode="user_guided",
        participants=[
            {"name": "glm", "session_configuration_id": "cc-glm"},
            {"name": "gpt", "session_configuration_id": "codex-gpt"},
        ],
    )


@pytest.mark.anyio
async def test_completed_discussion_writes_one_host_neutral_episode_and_no_note(
    tmp_path: Path,
) -> None:
    service, coordinator, opener, _artifacts = _build_system(tmp_path)
    created = await service.create(_request("episode"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    waiting = service.get(created["id"])
    service.finish(
        created["id"],
        VersionedCommand(command_id="finish", expected_version=waiting["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)

    completed = service.get(created["id"])
    episode_files = list((tmp_path / "memory" / "episodes").glob("*.md"))
    episode_text = episode_files[0].read_text(encoding="utf-8")
    with opener() as repository:
        internal = repository.get_discussion(created["id"])

    assert completed["status"] == "completed"
    assert len(episode_files) == 1
    assert internal.episode_id == f"discussion-{created['id']}"
    assert "source_kind: discussion" in episode_text
    assert "cc_session_id:" not in episode_text
    assert "answer-glm-round-1" in episode_text
    assert "answer-gpt-round-2" in episode_text
    assert not (tmp_path / "memory" / "notes").exists()


@pytest.mark.anyio
async def test_profile_batch_sees_only_top_level_user_messages(
    tmp_path: Path,
) -> None:
    service, coordinator, opener, _artifacts = _build_system(tmp_path)
    created = await service.create(_request("profile"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    waiting = service.get(created["id"])
    service.add_message(
        created["id"],
        AddDiscussionMessageRequest(
            command_id="supplement",
            expected_version=waiting["version"],
            body="补充：下一轮只比较可维护性",
        ),
    )
    source_calls: list[str] = []

    def host_factory(source_id: str, workdir: Path) -> FakeHost:
        """为每条候选写入只引用初始用户原话的建议草稿。"""

        source_calls.append(source_id)
        draft = {
            "suggestions": [
                {
                    "dimension": "methodology",
                    "body": "偏好真实运行验证",
                    "sources": ["真实运行比只看单测更可信"],
                    "rationale": "用户明确表达",
                }
            ]
        }
        (workdir / "suggestions-draft.json").write_text(
            json.dumps(draft, ensure_ascii=False),
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    await run_daily_distill(
        tmp_path / "memory",
        "http://unused",
        host_factory=host_factory,
        date_str="2026-08-06",
        discussion_repository_opener=opener,
        discussion_data_root=tmp_path / "data",
    )
    suggestions = load_suggestions(tmp_path / "memory")
    with opener() as repository:
        internal = repository.get_discussion(created["id"])

    assert len(source_calls) == 2
    assert all(
        call.startswith(f"discussion:{created['id']}:message:") for call in source_calls
    )
    assert len(suggestions) == 1
    assert "真实运行比只看单测更可信" in suggestions[0].sources
    assert all(message.profile_status == "processed" for message in internal.messages)
    assert all(
        "answer-" not in source for item in suggestions for source in item.sources
    )

    # 模拟建议队列已经写入、SQLite 水位提交失败后的重试。
    with opener() as repository, repository.transaction():
        repository.connection.execute(
            """
            UPDATE discussion_user_messages SET profile_status='pending'
            WHERE sequence=1 AND discussion_id=?
            """,
            (created["id"],),
        )
    await run_daily_distill(
        tmp_path / "memory",
        "http://unused",
        host_factory=host_factory,
        date_str="2026-08-07",
        discussion_repository_opener=opener,
        discussion_data_root=tmp_path / "data",
    )
    assert len(load_suggestions(tmp_path / "memory")) == 1


@pytest.mark.anyio
async def test_profile_rejects_sqlite_message_drift_without_advancing_watermark(
    tmp_path: Path,
) -> None:
    """Profile 入口发现 SQLite 正文副本漂移时登记对账且不处理消息。"""

    service, _, opener, _ = _build_system(tmp_path)
    created = await service.create(_request("profile-body-drift"))
    with opener() as repository, repository.transaction():
        repository.connection.execute(
            """
            UPDATE discussion_user_messages SET body='被篡改的 SQLite 副本'
            WHERE discussion_id=? AND sequence=1
            """,
            (created["id"],),
        )
    host_calls = 0

    def host_factory(source_id: str, workdir: Path) -> FakeHost:
        """漂移应在启动模型前被拒绝。"""

        del source_id, workdir
        nonlocal host_calls
        host_calls += 1
        return FakeHost([FINISHED])

    await run_daily_distill(
        tmp_path / "memory",
        "http://unused",
        host_factory=host_factory,
        date_str="2026-08-06",
        discussion_repository_opener=opener,
        discussion_data_root=tmp_path / "data",
    )

    with opener() as repository:
        message = repository.list_pending_profile_messages()[0]
        issue = repository.connection.execute(
            "SELECT issue_code FROM discussion_integrity_issues WHERE discussion_id=?",
            (created["id"],),
        ).fetchone()
    assert host_calls == 0
    assert message.profile_status == "pending"
    assert issue["issue_code"] == "ARTIFACT_INTEGRITY"
