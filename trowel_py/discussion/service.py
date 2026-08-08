"""实现研讨创建、轮次命令、公开投影和配置冻结边界。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.configuration.errors import ConfigurationError
from trowel_py.configuration.models import (
    CapabilityView,
    RuntimeKind,
    SessionConfigurationView,
)
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
from trowel_py.discussion.handoff import (
    HandoffSessionPort,
    build_handoff_prompt,
    transcript_api_path,
)
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
    ContinueDiscussionRequest,
    CreateDiscussionHandoffRequest,
    CreateDiscussionRequest,
    MarkDiscussionResultRequest,
    StopDiscussionRequest,
    VersionedCommand,
)

Clock = Callable[[], str]


@dataclass(frozen=True)
class ParticipantRuntimeIdentity:
    """保存参与者创建时可公开、可长期回看的运行身份。

    Attributes:
        connection_name: 设置域连接的展示名，例如 DeepSeek 或 PRO X20。
        effective_model: Claude 角色别名解析后的真实模型，Codex 为所选模型。
    """

    connection_name: str
    effective_model: str


def _now() -> str:
    """返回带微秒的本地 ISO 时间。"""

    return datetime.now().isoformat(timespec="microseconds")


class SessionConfigurationCatalog(Protocol):
    """约束 discussion 创建阶段读取设置域会话配置的能力。"""

    def get(self, configuration_id: str) -> SessionConfigurationView:
        """返回实时核对 availability 后的完整会话配置。"""
        ...

    def resolve(
        self,
        connection_id: str,
        *,
        model: str,
        effort: str | None,
    ) -> SessionConfigurationView:
        """按 Agent 同一入口校验未命名的连接、模型和强度组合。"""
        ...

    def runtime_identity(
        self,
        connection_id: str,
        *,
        model: str,
        effort: str | None,
    ) -> ParticipantRuntimeIdentity:
        """解析不含凭据的连接展示名和真实模型。"""
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

    def resolve(
        self,
        connection_id: str,
        *,
        model: str,
        effort: str | None,
    ) -> SessionConfigurationView:
        """复用 Agent runtime launch 校验并返回不持久化的冻结配置。

        Args:
            connection_id: 设置域连接 ID。
            model: 用户选择的模型或 Claude 角色别名。
            effort: 用户选择的思考强度。

        Returns:
            availability=available、id 为空的临时配置读模型。
        """

        connection = create_db(self._db_path)
        try:
            run_migrations(connection)
            launch = ConfigurationService(
                ConfigurationRepository(connection)
            ).resolve_runtime_launch(connection_id, model=model, effort=effort)
            return SessionConfigurationView(
                id="",
                version=0,
                name=launch.connection_name,
                runtime=RuntimeKind(launch.runtime.value),
                connection_id=launch.connection_id,
                connection_identity_version=launch.connection_identity_version,
                model=launch.model,
                effort=launch.effort,
                capability=CapabilityView(
                    status="verified",
                    version=launch.capability_version,
                    source=(
                        launch.capability_source
                        or "Agent runtime launch validation"
                    ),
                ),
                availability="available",
                disabled_reason=None,
            )
        finally:
            connection.close()

    def runtime_identity(
        self,
        connection_id: str,
        *,
        model: str,
        effort: str | None,
    ) -> ParticipantRuntimeIdentity:
        """从同一启动解析结果冻结连接名和角色映射后的真实模型。

        Args:
            connection_id: 设置域连接 ID。
            model: 用户选择的真实模型或 Claude 角色别名。
            effort: 用户选择的思考强度。

        Returns:
            不含 secret 的参与者运行身份。
        """

        connection = create_db(self._db_path)
        try:
            run_migrations(connection)
            launch = ConfigurationService(
                ConfigurationRepository(connection)
            ).resolve_runtime_launch(connection_id, model=model, effort=effort)
            return ParticipantRuntimeIdentity(
                connection_name=launch.connection_name,
                effective_model=launch.claude_role_models.get(
                    launch.model, launch.model
                ),
            )
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
        handoff_sessions: HandoffSessionPort | None = None,
        transcript_access_token_factory: Callable[[str], str | None] | None = None,
        clock: Clock = _now,
    ) -> None:
        """装配 service 需要的五个显式端口。

        Args:
            repository_opener: 每个命令使用的独立主库连接工厂。
            artifacts: 正文 artifact 存储。
            coordinator: participant worker 与恢复协调器。
            events: 持久事件唤醒总线。
            configuration_catalog: 设置域会话配置查询入口。
            handoff_sessions: 创建普通 Agent 并发送交接现场的端口。
            transcript_access_token_factory: 按只读 API 路径签发桌面实例级能力令牌。
            clock: 测试可替换的时间函数。
        """

        self._open_repository = repository_opener
        self._artifacts = artifacts
        self._coordinator = coordinator
        self._events = events
        self._configuration_catalog = configuration_catalog
        self._handoff_sessions = handoff_sessions
        self._transcript_access_token_factory = transcript_access_token_factory
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
        runtime_identities: list[ParticipantRuntimeIdentity] = []
        permissions: list[tuple[str | None, str | None]] = []
        for item in request.participants:
            try:
                configuration = (
                    self._configuration_catalog.get(item.session_configuration_id)
                    if item.session_configuration_id is not None
                    else self._configuration_catalog.resolve(
                        item.connection_id or "",
                        model=item.model or "",
                        effort=item.effort,
                    )
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
            try:
                runtime_identity = self._configuration_catalog.runtime_identity(
                    configuration.connection_id,
                    model=configuration.model,
                    effort=configuration.effort,
                )
            except ConfigurationError as exc:
                raise DiscussionError(
                    "DISCUSSION_CONFIGURATION_INVALID",
                    "参与者连接身份已经不可用",
                    status_code=422,
                ) from exc
            if configuration.runtime.value not in {"claude_code", "codex"}:
                raise DiscussionError(
                    "DISCUSSION_RUNTIME_UNSUPPORTED",
                    "研讨参与者只能使用 Claude Code 或 Codex",
                    status_code=422,
                )
            if configuration.runtime.value == "claude_code":
                if item.permission_preset is not None:
                    raise DiscussionError(
                        "DISCUSSION_PERMISSION_INVALID",
                        "Claude Code 参与者不能使用 Codex 权限预设",
                        status_code=422,
                    )
                permissions.append((item.permission_mode or "dontAsk", None))
            else:
                if item.permission_mode is not None:
                    raise DiscussionError(
                        "DISCUSSION_PERMISSION_INVALID",
                        "Codex 参与者不能使用 Claude Code 权限模式",
                        status_code=422,
                    )
                permissions.append((None, item.permission_preset or "read-only"))
            configurations.append(configuration)
            runtime_identities.append(runtime_identity)
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
                connection_name=runtime_identity.connection_name,
                model=configuration.model,
                effective_model=runtime_identity.effective_model,
                effort=configuration.effort,
                session_configuration_id=configuration.id or None,
                permission_mode=permission[0],
                permission_preset=permission[1],
                memory_enabled=item.memory_enabled,
                profile_enabled=item.profile_enabled,
                self_enabled=item.self_enabled,
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
            for position, (item, configuration, runtime_identity, permission) in enumerate(
                zip(
                    request.participants,
                    configurations,
                    runtime_identities,
                    permissions,
                    strict=True,
                )
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

    def get_transcript(self, discussion_id: str) -> str:
        """重建并读取一场研讨的完整公开记录。

        Args:
            discussion_id: 要读取的研讨 ID。

        Returns:
            只含用户原话和已共同发布轮次的 Markdown。
        """

        with self._open_repository() as repository:
            discussion = repository.get_discussion(discussion_id)
        transcript = self._artifacts.rebuild_transcript(discussion)
        return self._artifacts.read_text(
            transcript.relative_path,
            expected_sha256=transcript.sha256,
            expected_bytes=transcript.byte_count,
        )

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
            不含正文的事件摘要；客户端据此重新读取最新公开快照。
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
        command: ContinueDiscussionRequest,
    ) -> dict[str, Any]:
        """在公开边界按用户本次选择切换推进方式并开始下一轮。

        Args:
            discussion_id: 要继续的研讨 ID。
            command: 幂等身份、版本和后续推进方式。

        Returns:
            新普通轮创建后的公开 DTO。
        """

        with self._open_repository() as repository:
            discussion = repository.get_discussion(discussion_id)
        next_round = (discussion.active_round_number or 0) + 1
        max_rounds = (
            next_round + (command.additional_rounds or 1) - 1
            if command.progression_mode == "automatic"
            else None
        )
        result = self._begin_round(
            discussion_id,
            command,
            kind="regular",
            allowed_statuses={"waiting_user"},
            command_type="continue",
            progression_mode=command.progression_mode,
            max_rounds=max_rounds,
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

    def mark_result(
        self,
        discussion_id: str,
        request: MarkDiscussionResultRequest,
    ) -> dict[str, Any]:
        """把一个已公开结果加入或移出确定性交接现场。

        Args:
            discussion_id: 标记所属研讨 ID。
            request: 轮次、参与者、目标状态和并发身份。

        Returns:
            标记状态已经持久化的公开研讨 DTO。
        """

        request_hash = _request_hash("mark", request.model_dump(mode="json"))
        source_ref = _mark_source(request.round_number, request.participant_id)
        created_at = self._clock()
        with self._open_repository() as repository, repository.transaction():
            prior = repository.get_command_result(
                discussion_id,
                request.command_id,
                command_type="mark",
                request_hash=request_hash,
            )
            if prior is None:
                discussion = repository.get_discussion(discussion_id)
                round_record = next(
                    (
                        item
                        for item in discussion.rounds
                        if item.number == request.round_number
                    ),
                    None,
                )
                if round_record is None or round_record.status != "published":
                    raise DiscussionStateError("只有已公开结果可以标记")
                if request.participant_id not in {
                    item.participant_id for item in round_record.results
                }:
                    raise DiscussionError(
                        "DISCUSSION_PARTICIPANT_NOT_FOUND",
                        "找不到要标记的参与者结果",
                        status_code=422,
                    )
                version = repository.advance_version(
                    discussion_id,
                    expected_version=request.expected_version,
                    allowed_statuses=(
                        "running",
                        "waiting_user",
                        "completed",
                        "stopped",
                        "needs_reconcile",
                    ),
                    updated_at=created_at,
                )
                repository.set_user_mark(
                    discussion_id,
                    source_ref,
                    marked=request.marked,
                    created_at=created_at,
                )
                repository.append_event(
                    discussion_id,
                    "discussion_mark_changed",
                    version,
                    created_at=created_at,
                    round_number=request.round_number,
                )
                repository.record_command_result(
                    discussion_id,
                    request.command_id,
                    command_type="mark",
                    request_hash=request_hash,
                    result={"version": version, "marked": request.marked},
                    created_at=created_at,
                )
        self._events.publish(discussion_id)
        return self.get(discussion_id)

    async def handoff(
        self,
        discussion_id: str,
        request: CreateDiscussionHandoffRequest,
    ) -> dict[str, Any]:
        """确定性组装公开现场并创建一个普通 Agent 会话。

        Args:
            discussion_id: 要交接的研讨 ID。
            request: 普通 Agent 条件、命令身份和 expected version。

        Returns:
            新 Agent 会话、首轮 ID 和推进后的 discussion version。

        Raises:
            DiscussionRuntimeError: 应用未装配 handoff 端口或 Agent 接受失败。
        """

        if self._handoff_sessions is None:
            raise DiscussionRuntimeError("普通 Agent 交接服务尚未初始化")
        request_hash = _request_hash("handoff", request.model_dump(mode="json"))
        with self._open_repository() as repository:
            prior = repository.get_command_result(
                discussion_id,
                request.command_id,
                command_type="handoff",
                request_hash=request_hash,
            )
            if prior is not None and prior.get("status") == "started":
                return prior
            discussion = repository.get_discussion(discussion_id)
            marked_sources = repository.list_user_mark_sources(discussion_id)
        if discussion.status not in {"waiting_user", "completed", "stopped"}:
            raise DiscussionStateError("只有轮次间或已收口的研讨可以交给 Agent")
        transcript_path = transcript_api_path(discussion.id)
        transcript_access_token = (
            self._transcript_access_token_factory(transcript_path)
            if self._transcript_access_token_factory is not None
            else None
        )
        prompt = build_handoff_prompt(
            discussion,
            self._artifacts,
            marked_sources,
            transcript_access_token,
        )
        created_at = self._clock()
        if prior is None:
            with self._open_repository() as repository, repository.transaction():
                version = repository.advance_version(
                    discussion_id,
                    expected_version=request.expected_version,
                    allowed_statuses=("waiting_user", "completed", "stopped"),
                    updated_at=created_at,
                )
                repository.record_command_result(
                    discussion_id,
                    request.command_id,
                    command_type="handoff",
                    request_hash=request_hash,
                    result={"status": "reserved", "version": version},
                    created_at=created_at,
                )
        else:
            version = int(prior["version"])
        agent = request.agent
        session_request = CreateAgentSessionRequest(
            runtime=agent.runtime,
            connection_id=agent.connection_id,
            workdir=str(Path(agent.workdir).expanduser().resolve()),
            model=agent.model,
            effort=agent.effort,
            permission_mode=agent.permission_mode,
            permission_preset=agent.permission_preset,
            memory_enabled=agent.memory_enabled,
            profile_enabled=agent.profile_enabled,
            self_enabled=agent.self_enabled,
            session_kind="user",
            memory_eligibility=False,
            agent_mcp_enabled=False,
            owner_ref=(
                f"discussion:{discussion_id}:handoff:{request.command_id}:v1"
            ),
        )
        result = await self._handoff_sessions.create_and_start(
            request_id=f"discussion-handoff:{discussion_id}:{request.command_id}",
            request=session_request,
            prompt=prompt,
            instruction=request.instruction,
        )
        response = {
            "status": "started",
            "version": version,
            "discussion_id": discussion_id,
            "agent_session_id": result.agent_session_id,
            "turn_id": result.turn_id,
        }
        with self._open_repository() as repository, repository.transaction():
            repository.update_command_result(
                discussion_id,
                request.command_id,
                command_type="handoff",
                request_hash=request_hash,
                result=response,
            )
            repository.append_event(
                discussion_id,
                "discussion_handoff_created",
                version,
                created_at=self._clock(),
            )
        self._events.publish(discussion_id)
        return response

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
        progression_mode: str | None = None,
        max_rounds: int | None = None,
    ) -> dict[str, Any]:
        """文件先行创建公共输入，并在同一事务保存轮次和命令收据。

        Args:
            discussion_id: 所属研讨 ID。
            command: 幂等命令身份和 expected version。
            kind: regular 或 final。
            allowed_statuses: 本命令允许的 lifecycle 状态。
            command_type: command ledger 类型。
            progression_mode: 在本轮开始时切换的新推进方式。
            max_rounds: 自动模式新的绝对停止轮号；逐轮模式为 None。

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
                    progression_mode=progression_mode,
                    max_rounds=max_rounds,
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

        with self._open_repository() as repository:
            marked_sources = repository.list_user_mark_sources(discussion.id)
            handoffs = repository.list_handoff_results(discussion.id)
        participants = [
            {
                "id": item.id,
                "position": item.position,
                "name": item.name,
                "runtime": item.runtime.value,
                "connection_name": item.connection_name,
                "model": item.model,
                "effective_model": item.effective_model,
                "effort": item.effort,
                "permission_mode": item.permission_mode,
                "permission_preset": item.permission_preset,
                "memory_enabled": item.memory_enabled,
                "profile_enabled": item.profile_enabled,
                "self_enabled": item.self_enabled,
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
                    activity = (
                        json.loads(result.activity_json)
                        if result.activity_json
                        else {"tool_call_count": 0, "tool_names": {}, "subagent_count": 0}
                    )
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
                    activity = None
                slots.append(
                    {
                        "participant_id": result.participant_id,
                        "current_attempt_id": result.current_attempt_id,
                        "position": result.position,
                        "name": participant_by_id[result.participant_id].name,
                        "status": public_status,
                        "content": content,
                        "error_code": error_code,
                        "error_message": error_message,
                        "usage": usage,
                        "activity": activity,
                        "marked": _mark_source(
                            round_record.number, result.participant_id
                        ) in marked_sources,
                        "started_at": result.started_at,
                        # 观察者需要在共同公开前把成功轨迹折叠并显示真实耗时；
                        # 正文仍只来自 attempt 实时流，不从未公开 artifact 读取。
                        "completed_at": result.completed_at,
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
            "handoffs": handoffs,
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


def _mark_source(round_number: int, participant_id: str) -> str:
    """生成公开结果在 promotion 表中的稳定来源引用。"""

    return f"round:{round_number}:participant:{participant_id}"
