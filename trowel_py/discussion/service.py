"""实现研讨创建、轮次命令、公开投影和配置冻结边界。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from trowel_py.agent_host.binding import Runtime
from trowel_py.configuration.errors import ConfigurationError
from trowel_py.configuration.models import SessionConfigurationView
from trowel_py.configuration.repository import ConfigurationRepository
from trowel_py.configuration.service import ConfigurationService
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator, RepositoryOpener
from trowel_py.discussion.errors import (
    DiscussionError,
    DiscussionRuntimeError,
    DiscussionStateError,
    DiscussionVersionConflictError,
)
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.models import (
    Discussion,
    DiscussionParticipant,
    DiscussionRound,
    RoundKind,
    TargetScope,
    UserMessage,
)
from trowel_py.discussion.prompts import build_round_prompt
from trowel_py.discussion.schemas import (
    AddDiscussionMessageRequest,
    CreateDiscussionRequest,
    StopDiscussionRequest,
    VersionedCommand,
)

Clock = Callable[[], str]


def _now() -> str:
    """返回带微秒的本地 ISO 时间。"""

    return datetime.now().isoformat(timespec="microseconds")


class SessionConfigurationCatalog(Protocol):
    """约束 discussion 创建阶段读取设置域会话配置的能力。"""

    def get(self, configuration_id: str) -> SessionConfigurationView:
        """返回实时核对 availability 后的完整会话配置。"""
        ...


class SqliteSessionConfigurationCatalog:
    """每次查询用独立主库连接实时核对配置 availability。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        """保存测试可注入的主库路径。

        Args:
            db_path: 显式 SQLite 文件；正式运行省略。
        """

        self._db_path = db_path

    def get(self, configuration_id: str) -> SessionConfigurationView:
        """读取设置域会话配置并在返回前关闭 SQLite 连接。

        Args:
            configuration_id: 要解析的完整会话配置 ID。

        Returns:
            实时降级后的设置域读模型。
        """

        connection = create_db(self._db_path)
        try:
            run_migrations(connection)
            return ConfigurationService(
                ConfigurationRepository(connection)
            ).get_session_configuration(configuration_id)
        finally:
            connection.close()


class DiscussionService:
    """协调短事务命令、文件先行写入和后台 worker 唤醒。"""

    def __init__(
        self,
        repository_opener: RepositoryOpener,
        artifacts: DiscussionArtifactStore,
        coordinator: DiscussionCoordinator,
        events: DiscussionEventBus,
        configuration_catalog: SessionConfigurationCatalog,
        *,
        clock: Clock = _now,
    ) -> None:
        """装配 service 需要的五个显式端口。

        Args:
            repository_opener: 每个命令使用的独立主库连接工厂。
            artifacts: 正文 artifact 存储。
            coordinator: participant worker 与恢复协调器。
            events: 持久事件唤醒总线。
            configuration_catalog: 设置域会话配置查询入口。
            clock: 测试可替换的时间函数。
        """

        self._open_repository = repository_opener
        self._artifacts = artifacts
        self._coordinator = coordinator
        self._events = events
        self._configuration_catalog = configuration_catalog
        self._clock = clock

    async def create(self, request: CreateDiscussionRequest) -> dict[str, Any]:
        """验证配置、文件先行保存初始原话，再创建并认领 participant。

        Args:
            request: 已校验自动轮数与参与者数量的创建请求。

        Returns:
            不暴露 session/native/artifact 路径的公开研讨 DTO。

        Raises:
            DiscussionError: 工作目录、名称或配置不满足创建条件。
        """

        create_hash = _request_hash("create", request.model_dump(mode="json"))
        with self._open_repository() as repository:
            existing = repository.get_discussion_by_create_request(request.request_id)
        if existing is not None:
            if existing.create_request_hash != create_hash:
                raise DiscussionError(
                    "DISCUSSION_COMMAND_CONFLICT",
                    "同一创建请求标识已经用于另一场研讨",
                    status_code=409,
                )
            return self._public_discussion(existing)
        workdir = str(Path(request.workdir).expanduser().resolve())
        if not Path(workdir).is_dir():
            raise DiscussionError(
                "DISCUSSION_WORKDIR_NOT_FOUND",
                "研讨工作目录不存在",
                status_code=422,
            )
        names = [item.name for item in request.participants]
        if len(set(names)) != len(names):
            raise DiscussionError(
                "DISCUSSION_PARTICIPANT_NAME_DUPLICATE",
                "参与者名称不能重复",
                status_code=422,
            )
        configurations: list[SessionConfigurationView] = []
        for item in request.participants:
            try:
                configuration = self._configuration_catalog.get(
                    item.session_configuration_id
                )
            except ConfigurationError as exc:
                raise DiscussionError(
                    "DISCUSSION_CONFIGURATION_INVALID",
                    "参与者会话配置不存在或已经不可用",
                    status_code=422,
                ) from exc
            if configuration.availability != "available":
                raise DiscussionError(
                    "DISCUSSION_CONFIGURATION_STALE",
                    "参与者会话配置已经过期",
                    status_code=409,
                )
            if configuration.runtime.value not in {"claude_code", "codex"}:
                raise DiscussionError(
                    "DISCUSSION_RUNTIME_UNSUPPORTED",
                    "研讨参与者只能使用 Claude Code 或 Codex",
                    status_code=422,
                )
            configurations.append(configuration)
        discussion_id = _stable_id("discussion", request.request_id)
        created_at = self._clock()
        participant_records = tuple(
            DiscussionParticipant(
                id=_stable_id("participant", discussion_id, str(position)),
                discussion_id=discussion_id,
                position=position,
                name=item.name,
                runtime=Runtime(configuration.runtime.value),
                connection_id=configuration.connection_id,
                model=configuration.model,
                effort=configuration.effort,
                session_configuration_id=configuration.id,
                connection_identity_version=configuration.connection_identity_version,
                owner_ref=(f"discussion:{discussion_id}:participant:{position}:v1"),
                agent_session_id=None,
                native_session_id=None,
                status="pending",
                capability_version=configuration.capability.version,
                capability_source=configuration.capability.source,
                created_at=created_at,
                updated_at=created_at,
            )
            for position, (item, configuration) in enumerate(
                zip(request.participants, configurations, strict=True)
            )
        )
        message_id = _stable_id("message", discussion_id, "initial")
        message_ref = self._artifacts.write_user_message(
            discussion_id=discussion_id,
            message_id=message_id,
            sequence=1,
            after_round_number=0,
            target_scope="all",
            target_participant_id=None,
            body=request.topic,
        )
        initial_message = UserMessage(
            id=message_id,
            discussion_id=discussion_id,
            sequence=1,
            after_round_number=0,
            target_scope="all",
            target_participant_id=None,
            body=request.topic,
            message_artifact=message_ref.relative_path,
            message_sha256=message_ref.sha256,
            message_bytes=message_ref.byte_count,
            profile_status="pending",
            profile_source_id=f"discussion:{discussion_id}:message:{message_id}",
            created_at=created_at,
        )
        discussion = Discussion(
            id=discussion_id,
            create_request_id=request.request_id,
            create_request_hash=create_hash,
            topic=request.topic,
            workdir=workdir,
            progression_mode=request.progression_mode,
            max_rounds=request.max_rounds,
            status="draft",
            version=1,
            active_round_number=None,
            episode_id=None,
            created_at=created_at,
            updated_at=created_at,
            completed_at=None,
            stopped_at=None,
            deleted_at=None,
        )
        try:
            with self._open_repository() as repository, repository.transaction():
                repository.insert_discussion(
                    discussion,
                    participant_records,
                    initial_message,
                )
        except sqlite3.IntegrityError:
            with self._open_repository() as repository:
                concurrent = repository.get_discussion_by_create_request(
                    request.request_id
                )
            if concurrent is None or concurrent.create_request_hash != create_hash:
                raise DiscussionError(
                    "DISCUSSION_COMMAND_CONFLICT",
                    "同一创建请求标识已经用于另一场研讨",
                    status_code=409,
                ) from None
            discussion_id = concurrent.id
        try:
            ready = await self._coordinator.ensure_participants(discussion_id)
        except Exception:  # noqa: BLE001 - durable object remains visible for recovery.
            with self._open_repository() as repository, repository.transaction():
                repository.mark_needs_reconcile(
                    discussion_id,
                    updated_at=self._clock(),
                )
            with self._open_repository() as repository:
                ready = repository.get_discussion(discussion_id)
        self._events.publish(discussion_id)
        return self._public_discussion(ready)

    def list(self) -> list[dict[str, Any]]:
        """返回最近研讨的公开 DTO 列表。"""

        with self._open_repository() as repository:
            discussions = repository.list_discussions()
        result = []
        for discussion in discussions:
            try:
                result.append(self._public_discussion(discussion))
            except (OSError, UnicodeError, ValueError):
                self._record_integrity_issue(discussion.id)
                raise
        return result

    def get(self, discussion_id: str) -> dict[str, Any]:
        """返回一场研讨的公开 DTO。

        Args:
            discussion_id: 要查询的研讨 ID。

        Returns:
            未公开轮不含正文、错误和 artifact 引用的 DTO。
        """

        with self._open_repository() as repository:
            discussion = repository.get_discussion(discussion_id)
        try:
            return self._public_discussion(discussion)
        except (OSError, UnicodeError, ValueError):
            self._record_integrity_issue(discussion_id)
            raise

    def _record_integrity_issue(self, discussion_id: str) -> None:
        """幂等登记 artifact 读取失败，供启动补偿和人工诊断。

        Args:
            discussion_id: 需要重新验真的研讨 ID。
        """

        with self._open_repository() as repository, repository.transaction():
            repository.record_integrity_issue(
                discussion_id,
                detected_at=self._clock(),
            )

    def list_events(
        self, discussion_id: str, *, after_sequence: int
    ) -> tuple[dict[str, Any], ...]:
        """返回 discussion SSE 可断线重放的持久状态事件。

        Args:
            discussion_id: 所属研讨 ID。
            after_sequence: 客户端已经看到的最大 sequence。

        Returns:
            不含正文的事件摘要；round_published 由客户端随后 GET 最新快照。
        """

        with self._open_repository() as repository:
            repository.get_discussion(discussion_id)
            return repository.list_events(
                discussion_id,
                after_sequence=after_sequence,
            )

    def start(
        self,
        discussion_id: str,
        command: VersionedCommand,
    ) -> dict[str, Any]:
        """从 draft 创建第一轮并启动后台 participant worker。

        Args:
            discussion_id: 要开始的研讨 ID。
            command: 幂等命令 ID 和 expected version。

        Returns:
            创建 running round 后的公开 DTO。
        """

        result = self._begin_round(
            discussion_id,
            command,
            kind="regular",
            allowed_statuses={"draft"},
            command_type="start",
        )
        self._coordinator.schedule(discussion_id)
        return result

    def continue_round(
        self,
        discussion_id: str,
        command: VersionedCommand,
    ) -> dict[str, Any]:
        """在 user-guided 研讨等待状态下开始下一普通轮。

        Args:
            discussion_id: 要继续的研讨 ID。
            command: 幂等命令 ID 和 expected version。

        Returns:
            新普通轮创建后的公开 DTO。
        """

        result = self._begin_round(
            discussion_id,
            command,
            kind="regular",
            allowed_statuses={"waiting_user"},
            command_type="continue",
        )
        self._coordinator.schedule(discussion_id)
        return result

    def finish(
        self,
        discussion_id: str,
        command: VersionedCommand,
    ) -> dict[str, Any]:
        """从等待状态创建最后一轮独立收尾发言。

        Args:
            discussion_id: 要收尾的研讨 ID。
            command: 幂等命令 ID 和 expected version。

        Returns:
            final round 创建后的公开 DTO。
        """

        result = self._begin_round(
            discussion_id,
            command,
            kind="final",
            allowed_statuses={"waiting_user"},
            command_type="finish",
        )
        self._coordinator.schedule(discussion_id)
        return result

    def add_message(
        self,
        discussion_id: str,
        request: AddDiscussionMessageRequest,
    ) -> dict[str, Any]:
        """文件先行保存顶层用户原话，再原子推进消息序号和命令收据。

        Args:
            discussion_id: 所属研讨 ID。
            request: 用户补充、目标和并发身份。

        Returns:
            加入消息后的公开 DTO。
        """

        request_hash = _request_hash(
            "message",
            request.model_dump(mode="json"),
        )
        with self._open_repository() as repository:
            prior = repository.get_command_result(
                discussion_id,
                request.command_id,
                command_type="message",
                request_hash=request_hash,
            )
            if prior is not None:
                return self.get(discussion_id)
            discussion = repository.get_discussion(discussion_id)
            sequence = repository.next_message_sequence(discussion_id)
        if discussion.status != "waiting_user":
            raise DiscussionStateError("只有两轮之间才能补充用户消息")
        target_scope: TargetScope = "all"
        target_id = request.target_participant_id
        if target_id is not None:
            if target_id not in {item.id for item in discussion.participants}:
                raise DiscussionError(
                    "DISCUSSION_PARTICIPANT_NOT_FOUND",
                    "找不到补充消息的目标参与者",
                    status_code=422,
                )
            target_scope = "participant"
        message_id = _stable_id("message", discussion_id, request.command_id)
        created_at = self._clock()
        message_ref = self._artifacts.write_user_message(
            discussion_id=discussion_id,
            message_id=message_id,
            sequence=sequence,
            after_round_number=discussion.active_round_number or 0,
            target_scope=target_scope,
            target_participant_id=target_id,
            body=request.body,
        )
        message = UserMessage(
            id=message_id,
            discussion_id=discussion_id,
            sequence=sequence,
            after_round_number=discussion.active_round_number or 0,
            target_scope=target_scope,
            target_participant_id=target_id,
            body=request.body,
            message_artifact=message_ref.relative_path,
            message_sha256=message_ref.sha256,
            message_bytes=message_ref.byte_count,
            profile_status="pending",
            profile_source_id=f"discussion:{discussion_id}:message:{message_id}",
            created_at=created_at,
        )
        with self._open_repository() as repository, repository.transaction():
            prior = repository.get_command_result(
                discussion_id,
                request.command_id,
                command_type="message",
                request_hash=request_hash,
            )
            if prior is None:
                version = repository.add_user_message(
                    discussion_id,
                    message,
                    expected_version=request.expected_version,
                    updated_at=created_at,
                )
                repository.record_command_result(
                    discussion_id,
                    request.command_id,
                    command_type="message",
                    request_hash=request_hash,
                    result={"version": version, "message_id": message_id},
                    created_at=created_at,
                )
        self._events.publish(discussion_id)
        return self.get(discussion_id)

    async def stop_discussion(
        self,
        discussion_id: str,
        request: StopDiscussionRequest,
    ) -> dict[str, Any]:
        """先提交停止事实，再中断/关闭 participant，未知资源保留待对账。

        Args:
            discussion_id: 要停止的研讨 ID。
            request: 命令身份、版本和停止原因。

        Returns:
            资源收敛后的公开 DTO。
        """

        request_hash = _request_hash("stop", request.model_dump(mode="json"))
        with self._open_repository() as repository, repository.transaction():
            prior = repository.get_command_result(
                discussion_id,
                request.command_id,
                command_type="stop",
                request_hash=request_hash,
            )
            if prior is None:
                version = repository.stop_discussion(
                    discussion_id,
                    expected_version=request.expected_version,
                    stopped_at=self._clock(),
                    reason=request.reason,
                )
                repository.record_command_result(
                    discussion_id,
                    request.command_id,
                    command_type="stop",
                    request_hash=request_hash,
                    result={"version": version},
                    created_at=self._clock(),
                )
        self._events.publish(discussion_id)
        all_closed = await self._coordinator.stop_discussion_sessions(discussion_id)
        if not all_closed:
            return self.get(discussion_id)
        if not self._coordinator.finalize_episode(discussion_id):
            raise DiscussionRuntimeError(
                "研讨已经停止，但聚合 Episode 尚未完成写入，请重试停止命令"
            )
        return self.get(discussion_id)

    def resume(
        self,
        discussion_id: str,
        command: VersionedCommand,
    ) -> dict[str, Any]:
        """显式重试 needs_reconcile 研讨；应用启动恢复不需要用户调用。

        Args:
            discussion_id: 要恢复的研讨 ID。
            command: 幂等命令 ID 和 expected version。

        Returns:
            已切回 running 的公开 DTO。
        """

        request_hash = _request_hash("resume", command.model_dump(mode="json"))
        should_schedule = False
        with self._open_repository() as repository, repository.transaction():
            prior = repository.get_command_result(
                discussion_id,
                command.command_id,
                command_type="resume",
                request_hash=request_hash,
            )
            if prior is None:
                discussion = repository.get_discussion(discussion_id)
                if discussion.status != "needs_reconcile":
                    raise DiscussionStateError("这场研讨当前不需要恢复")
                if discussion.version != command.expected_version:
                    raise DiscussionVersionConflictError(discussion.version)
                if discussion.active_round_number is None:
                    version = discussion.version
                else:
                    version = repository.resume_existing_round(
                        discussion_id,
                        updated_at=self._clock(),
                    )
                repository.record_command_result(
                    discussion_id,
                    command.command_id,
                    command_type="resume",
                    request_hash=request_hash,
                    result={"version": version},
                    created_at=self._clock(),
                )
                should_schedule = True
        self._events.publish(discussion_id)
        if should_schedule:
            self._coordinator.schedule(discussion_id, recovering=True)
        return self.get(discussion_id)

    async def delete(
        self,
        discussion_id: str,
        command: VersionedCommand,
    ) -> dict[str, Any]:
        """关闭残余 participant 后软删除研讨，保留聚合 Episode provenance。

        Args:
            discussion_id: 要删除的已收口研讨 ID。
            command: 幂等命令 ID 和 expected version。

        Returns:
            只含 deleted 和最终 version 的 tombstone。
        """

        request_hash = _request_hash("delete", command.model_dump(mode="json"))
        with self._open_repository() as repository:
            prior = repository.get_command_result(
                discussion_id,
                command.command_id,
                command_type="delete",
                request_hash=request_hash,
            )
            if prior is not None:
                return {"id": discussion_id, "deleted": True, **prior}
            discussion = repository.get_discussion(discussion_id)
        if discussion.status not in {"completed", "stopped"}:
            raise DiscussionStateError("只有已经完成或停止的研讨可以删除")
        if discussion.version != command.expected_version:
            raise DiscussionVersionConflictError(discussion.version)
        closed = await self._coordinator.stop_discussion_sessions(discussion_id)
        if not closed:
            raise DiscussionRuntimeError("研讨参与者资源尚未完成对账，暂时不能删除")
        if not self._coordinator.finalize_episode(discussion_id):
            raise DiscussionRuntimeError("聚合 Episode 尚未完成写入，暂时不能删除")
        with self._open_repository() as repository, repository.transaction():
            prior = repository.get_command_result(
                discussion_id,
                command.command_id,
                command_type="delete",
                request_hash=request_hash,
            )
            if prior is not None:
                return {"id": discussion_id, "deleted": True, **prior}
            latest = repository.get_discussion(discussion_id)
            version = repository.soft_delete(
                discussion_id,
                expected_version=latest.version,
                deleted_at=self._clock(),
            )
            result = {"version": version}
            repository.record_command_result(
                discussion_id,
                command.command_id,
                command_type="delete",
                request_hash=request_hash,
                result=result,
                created_at=self._clock(),
            )
        self._events.publish(discussion_id)
        return {"id": discussion_id, "deleted": True, "version": version}

    def _begin_round(
        self,
        discussion_id: str,
        command: VersionedCommand,
        *,
        kind: RoundKind,
        allowed_statuses: set[str],
        command_type: str,
    ) -> dict[str, Any]:
        """文件先行创建公共输入，并在同一事务保存轮次和命令收据。

        Args:
            discussion_id: 所属研讨 ID。
            command: 幂等命令身份和 expected version。
            kind: regular 或 final。
            allowed_statuses: 本命令允许的 lifecycle 状态。
            command_type: command ledger 类型。

        Returns:
            新 running round 的公开 DTO。
        """

        request_hash = _request_hash(command_type, command.model_dump(mode="json"))
        with self._open_repository() as repository:
            prior = repository.get_command_result(
                discussion_id,
                command.command_id,
                command_type=command_type,
                request_hash=request_hash,
            )
            if prior is not None:
                return self.get(discussion_id)
            discussion = repository.get_discussion(discussion_id)
        if discussion.status not in allowed_statuses:
            raise DiscussionStateError("当前研讨状态不能开始这一轮")
        if discussion.version != command.expected_version:
            with self._open_repository() as repository, repository.transaction():
                repository.advance_version(
                    discussion_id,
                    expected_version=command.expected_version,
                    allowed_statuses=tuple(allowed_statuses),
                    updated_at=self._clock(),
                )
        round_number = (discussion.active_round_number or 0) + 1
        try:
            prompt = build_round_prompt(
                discussion,
                round_number=round_number,
                kind=kind,
                artifacts=self._artifacts,
            )
        except (OSError, UnicodeError, ValueError):
            self._record_integrity_issue(discussion_id)
            raise
        input_ref = self._artifacts.write_round_input(
            discussion_id=discussion_id,
            round_number=round_number,
            kind=kind,
            prompt=prompt.text,
        )
        created_at = self._clock()
        round_record = DiscussionRound(
            id=_stable_id("round", discussion_id, str(round_number)),
            discussion_id=discussion_id,
            number=round_number,
            kind=kind,
            status="running",
            public_context_hash=prompt.sha256,
            input_artifact=input_ref.relative_path,
            input_bytes=input_ref.byte_count,
            publication_artifact=None,
            publication_sha256=None,
            publication_bytes=None,
            started_at=created_at,
            published_at=None,
            stop_reason=None,
        )
        with self._open_repository() as repository, repository.transaction():
            prior = repository.get_command_result(
                discussion_id,
                command.command_id,
                command_type=command_type,
                request_hash=request_hash,
            )
            if prior is None:
                version = repository.create_round(
                    discussion_id,
                    round_record,
                    [item.id for item in discussion.participants],
                    expected_version=command.expected_version,
                    updated_at=created_at,
                )
                repository.record_command_result(
                    discussion_id,
                    command.command_id,
                    command_type=command_type,
                    request_hash=request_hash,
                    result={"version": version, "round_id": round_record.id},
                    created_at=created_at,
                )
        self._events.publish(discussion_id)
        return self.get(discussion_id)

    def _public_discussion(self, discussion: Discussion) -> dict[str, Any]:
        """移除所有 session/native/artifact/attempt 身份并执行共同发布可见性。

        Args:
            discussion: SQLite 完整内部聚合。

        Returns:
            API、SSE 后续 GET 和前端都可安全消费的公开 DTO。
        """

        participants = [
            {
                "id": item.id,
                "position": item.position,
                "name": item.name,
                "runtime": item.runtime.value,
                "model": item.model,
                "effort": item.effort,
                "status": item.status,
            }
            for item in discussion.participants
        ]
        participant_by_id = {item.id: item for item in discussion.participants}
        rounds = []
        for round_record in discussion.rounds:
            published = round_record.status == "published"
            if published:
                self._artifacts.verify_publication(discussion, round_record)
            terminal_count = sum(
                item.status not in {"pending", "running", "needs_reconcile"}
                for item in round_record.results
            )
            slots = []
            for result in round_record.results:
                public_status: str
                if published:
                    content = (
                        self._artifacts.read_text(
                            result.output_artifact,
                            expected_sha256=result.output_sha256,
                            expected_bytes=result.output_bytes,
                        )
                        if result.status == "succeeded" and result.output_artifact
                        else None
                    )
                    public_status = result.status
                    error_code = result.error_code
                    error_message = result.error_message
                    usage = json.loads(result.usage_json) if result.usage_json else None
                else:
                    content = None
                    public_status = (
                        result.status
                        if result.status in {"pending", "running", "needs_reconcile"}
                        else "sealed"
                    )
                    error_code = None
                    error_message = None
                    usage = None
                slots.append(
                    {
                        "participant_id": result.participant_id,
                        "position": result.position,
                        "name": participant_by_id[result.participant_id].name,
                        "status": public_status,
                        "content": content,
                        "error_code": error_code,
                        "error_message": error_message,
                        "usage": usage,
                        "started_at": result.started_at,
                        "completed_at": result.completed_at if published else None,
                    }
                )
            rounds.append(
                {
                    "id": round_record.id,
                    "number": round_record.number,
                    "kind": round_record.kind,
                    "status": round_record.status,
                    "public_context_hash": round_record.public_context_hash,
                    "total_participants": len(round_record.results),
                    "terminal_participants": terminal_count,
                    "started_at": round_record.started_at,
                    "published_at": round_record.published_at,
                    "stop_reason": round_record.stop_reason,
                    "participants": slots,
                }
            )
        return {
            "id": discussion.id,
            "topic": discussion.topic,
            "workdir": discussion.workdir,
            "progression_mode": discussion.progression_mode,
            "max_rounds": discussion.max_rounds,
            "round_timeout_seconds": None,
            "budget": None,
            "status": discussion.status,
            "version": discussion.version,
            "active_round_number": discussion.active_round_number,
            "created_at": discussion.created_at,
            "updated_at": discussion.updated_at,
            "completed_at": discussion.completed_at,
            "stopped_at": discussion.stopped_at,
            "participants": participants,
            "messages": [
                {
                    "id": item.id,
                    "sequence": item.sequence,
                    "after_round_number": item.after_round_number,
                    "author_role": "user",
                    "target_scope": item.target_scope,
                    "target_participant_id": item.target_participant_id,
                    "body": self._artifacts.read_user_message_body(item),
                    "created_at": item.created_at,
                }
                for item in discussion.messages
            ],
            "rounds": rounds,
        }


def _request_hash(command_type: str, payload: dict[str, Any]) -> str:
    """计算命令幂等对账指纹，不把请求正文写入 ledger。

    Args:
        command_type: 命令类型。
        payload: Pydantic 规范化后的请求字典。

    Returns:
        命令类型和完整 payload 的 SHA-256。
    """

    body = json.dumps(
        {"type": command_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(body).hexdigest()


def _stable_id(kind: str, *parts: str) -> str:
    """从领域身份生成不泄漏正文的 32 字符稳定 ID。

    Args:
        kind: ID 所属对象类型。
        parts: 已有的稳定领域身份，不得传入正文。

    Returns:
        SHA-256 前 32 个十六进制字符。
    """

    raw = "\x1f".join((kind, *parts)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]
