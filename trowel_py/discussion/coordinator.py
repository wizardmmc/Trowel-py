"""以持久轮次、attempt 和共同发布边界协调多位原生 Agent。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from trowel_py.agent_host.binding import Runtime
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.errors import DiscussionRuntimeError
from trowel_py.discussion.models import (
    Discussion,
    DiscussionParticipant,
    DiscussionRound,
)
from trowel_py.discussion.participant_sessions import ParticipantSessionPort
from trowel_py.discussion.prompts import build_round_prompt
from trowel_py.discussion.repository import DiscussionRepository
from trowel_py.discussion.state_machine import (
    round_ready_to_publish,
    should_automatically_continue,
    status_after_publication,
)

RepositoryOpener = Callable[[], AbstractContextManager[DiscussionRepository]]
Clock = Callable[[], str]


def _now() -> str:
    """返回带微秒的本地 ISO 时间，便于与现有 binding 时间排序。"""

    return datetime.now().isoformat(timespec="microseconds")


class DiscussionCoordinator:
    """持有有界 participant worker、崩溃恢复和原子共同发布流程。"""

    def __init__(
        self,
        repository_opener: RepositoryOpener,
        artifacts: DiscussionArtifactStore,
        sessions: ParticipantSessionPort,
        events: DiscussionEventBus,
        episode_writer: DiscussionEpisodeWriter,
        *,
        max_running_participants: int = 5,
        clock: Clock = _now,
    ) -> None:
        """装配协调器的持久化、文件、runtime 和事件端口。

        Args:
            repository_opener: 每次短操作打开独立 SQLite 连接的工厂。
            artifacts: discussion 私有 artifact 存储。
            sessions: participant 原生会话端口。
            events: 只发送持久状态变化信号的总线。
            episode_writer: 收口时写唯一聚合 Episode 的确定性写入器。
            max_running_participants: 物理同时运行上限；逻辑轮仍包含全部参与者。
            clock: 测试可替换的 ISO 时间函数。
        """

        if max_running_participants < 1:
            raise ValueError("discussion running limit must be positive")
        self._open_repository = repository_opener
        self._artifacts = artifacts
        self._sessions = sessions
        self._events = events
        self._episode_writer = episode_writer
        self._semaphore = asyncio.Semaphore(max_running_participants)
        self._clock = clock
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._active_sessions: dict[str, set[str]] = {}
        self._accepting = True

    async def start(self) -> None:
        """扫描应用上次退出时未完成的研讨并自动继续同一逻辑轮。"""

        with self._open_repository() as repository:
            discussion_ids = repository.list_recoverable_discussion_ids()
            pending_episode_ids = repository.list_pending_episode_discussion_ids()
            resource_reconcile_ids = (
                repository.list_terminal_resource_reconcile_discussion_ids()
            )
            transcript_rebuild_ids = (
                repository.list_discussion_ids_for_transcript_rebuild()
            )
            integrity_issue_ids = repository.list_integrity_issue_discussion_ids()
        for discussion_id in integrity_issue_ids:
            try:
                with self._open_repository() as repository:
                    discussion = repository.get_discussion(discussion_id)
                self._artifacts.verify_discussion(discussion)
            except Exception:  # noqa: BLE001 - 修复前保留 outbox，不阻断其他恢复。
                continue
            with self._open_repository() as repository, repository.transaction():
                repository.clear_integrity_issue(discussion_id)
        for discussion_id in transcript_rebuild_ids:
            try:
                with self._open_repository() as repository:
                    discussion = repository.get_discussion(discussion_id)
                self._rebuild_transcript(discussion)
            except Exception:  # noqa: BLE001 - transcript 是可重建缓存，其他 outbox 继续跑。
                pass
        resource_results: dict[str, bool] = {}
        for discussion_id in resource_reconcile_ids:
            resource_results[
                discussion_id
            ] = await self._close_idle_participant_sessions(discussion_id)
        for discussion_id in pending_episode_ids:
            with self._open_repository() as repository:
                terminal = repository.get_discussion(discussion_id)
            if terminal.status == "stopped" and not resource_results.get(
                discussion_id,
                True,
            ):
                continue
            self.finalize_episode(discussion_id)
        for discussion_id in discussion_ids:
            with self._open_repository() as repository, repository.transaction():
                discussion = repository.get_discussion(discussion_id)
                if discussion.status == "running":
                    repository.mark_running_attempts_host_lost(
                        discussion_id,
                        completed_at=self._clock(),
                    )
                    repository.mark_needs_reconcile(
                        discussion_id,
                        updated_at=self._clock(),
                    )
            self.schedule(discussion_id, recovering=True)

    def schedule(self, discussion_id: str, *, recovering: bool = False) -> None:
        """幂等启动一场研讨当前轮的后台协调任务。

        Args:
            discussion_id: 要运行或恢复的研讨 ID。
            recovering: 是否允许为旧未完成槽位创建新 attempt。
        """

        if not self._accepting:
            return
        current = self._tasks.get(discussion_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(
            self._run_discussion(discussion_id, recovering=recovering),
            name=f"discussion-{discussion_id}",
        )
        self._tasks[discussion_id] = task

        def discard(completed: asyncio.Task[None]) -> None:
            """任务完成后移除索引，并把意外异常转成 durable 待对账状态。"""

            if self._tasks.get(discussion_id) is completed:
                self._tasks.pop(discussion_id, None)
            if completed.cancelled():
                return
            try:
                failure = completed.exception()
            except asyncio.CancelledError:
                return
            if failure is None or not self._accepting:
                return
            try:
                with self._open_repository() as repository, repository.transaction():
                    discussion = repository.get_discussion(discussion_id)
                    if discussion.status != "running":
                        return
                    repository.mark_running_attempts_needs_reconcile(
                        discussion_id,
                        completed_at=self._clock(),
                    )
                    repository.mark_needs_reconcile(
                        discussion_id,
                        updated_at=self._clock(),
                    )
            except Exception:  # noqa: BLE001 - 下次应用启动仍会扫描 running 状态。
                return
            self._events.publish(discussion_id)

        task.add_done_callback(discard)

    async def wait_idle(self, discussion_id: str) -> None:
        """等待指定研讨当前后台任务结束，主要供测试和受控关闭使用。

        Args:
            discussion_id: 要等待的研讨 ID。
        """

        task = self._tasks.get(discussion_id)
        if task is not None:
            await asyncio.shield(task)

    async def ensure_participants(self, discussion_id: str) -> Discussion:
        """幂等创建或恢复所有 participant binding，并回写 native 恢复身份。

        Args:
            discussion_id: 所属研讨 ID。

        Returns:
            完成会话认领后的最新研讨聚合。
        """

        lock = self._locks.setdefault(discussion_id, asyncio.Lock())
        async with lock:
            with self._open_repository() as repository:
                discussion = repository.get_discussion(discussion_id)
            for participant in discussion.participants:
                if participant.status == "closed":
                    continue
                session = await self._sessions.ensure_session(
                    participant,
                    workdir=discussion.workdir,
                )
                with self._open_repository() as repository, repository.transaction():
                    repository.bind_participant_session(
                        participant.id,
                        agent_session_id=session.agent_session_id,
                        native_session_id=session.native_session_id,
                        updated_at=self._clock(),
                    )
            with self._open_repository() as repository:
                return repository.get_discussion(discussion_id)

    async def stop_discussion_sessions(self, discussion_id: str) -> bool:
        """中断活动 turn 并核验关闭全部 participant 会话。

        Args:
            discussion_id: 要收敛资源的研讨 ID。

        Returns:
            所有已登记 participant binding 都已关闭时为 True。
        """

        with self._open_repository() as repository:
            discussion = repository.get_discussion(
                discussion_id,
                include_deleted=True,
            )
        active = set(self._active_sessions.get(discussion_id, ()))
        for session_id in active:
            try:
                await self._sessions.interrupt(session_id)
            except Exception:  # noqa: BLE001 - 后续 close 仍能给出可对账结果。
                pass
        task = self._tasks.get(discussion_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        all_closed = True
        resource_state_changed = False
        for participant in discussion.participants:
            closed, changed = await self._close_participant_binding(
                discussion,
                participant,
            )
            all_closed = all_closed and closed
            resource_state_changed = resource_state_changed or changed
        self._active_sessions.pop(discussion_id, None)
        if resource_state_changed:
            self._events.publish(discussion_id)
        return all_closed

    def finalize_episode(self, discussion_id: str) -> bool:
        """为已收口研讨重建 transcript 并幂等登记聚合 Episode。

        文件写在 SQLite 引用之前。写入失败时 ``episode_id`` 保持空值，应用下次
        启动会再次扫描；调用方据返回值决定是否需要展示待对账状态。

        Args:
            discussion_id: 已 completed 或 stopped 的研讨 ID。

        Returns:
            Episode 文件与 SQLite 引用都已落盘时为 True；写入失败时为 False。
        """

        with self._open_repository() as repository:
            discussion = repository.get_discussion(discussion_id)
        if discussion.episode_id is not None:
            return True
        try:
            self._rebuild_transcript(discussion)
            episode_id = self._episode_writer.write(discussion)
        except Exception:  # noqa: BLE001 - episode_id 留空即是可重试 outbox。
            return False
        with self._open_repository() as repository, repository.transaction():
            stored = repository.set_episode_id(
                discussion_id,
                episode_id,
                updated_at=self._clock(),
            )
        return stored

    async def _close_idle_participant_sessions(self, discussion_id: str) -> bool:
        """关闭已无运行 turn 的 participant binding，不操作当前协调任务。

        Args:
            discussion_id: 已完成研讨 ID。

        Returns:
            所有已登记 binding 都确认关闭时为 True。
        """

        with self._open_repository() as repository:
            discussion = repository.get_discussion(discussion_id)
        all_closed = True
        resource_state_changed = False
        for participant in discussion.participants:
            closed, changed = await self._close_participant_binding(
                discussion,
                participant,
            )
            all_closed = all_closed and closed
            resource_state_changed = resource_state_changed or changed
        self._active_sessions.pop(discussion_id, None)
        if resource_state_changed:
            self._events.publish(discussion_id)
        return all_closed

    async def _close_participant_binding(
        self,
        discussion: Discussion,
        participant: DiscussionParticipant,
    ) -> tuple[bool, bool]:
        """按 owner_ref 对账并关闭单个 participant，随后提交关闭事实。

        Args:
            discussion: 当前研讨快照，用于区分 stopped 控制面取消。
            participant: 要关闭且可能带 stale SQLite session ID 的参与者。

        Returns:
            第一项表示资源已经关闭，第二项表示公开状态发生变化。
        """

        try:
            session_id = self._sessions.resolve_owned_session_id(participant)
            closed = (
                True if session_id is None else await self._sessions.close(session_id)
            )
        except Exception:  # noqa: BLE001 - owner 冲突或关闭失败必须保守留待对账。
            closed = False
        completed_at = self._clock()
        state_changed = False
        with self._open_repository() as repository, repository.transaction():
            if closed:
                state_changed = (
                    repository.connection.execute(
                        """
                    UPDATE discussion_participants
                    SET status='closed', updated_at=? WHERE id=? AND status!='closed'
                    """,
                        (completed_at, participant.id),
                    ).rowcount
                    > 0
                )
                if discussion.status == "stopped":
                    state_changed = (
                        repository.mark_stopped_participant_cancelled(
                            discussion.id,
                            participant.id,
                            completed_at=completed_at,
                        )
                        or state_changed
                    )
            else:
                if participant.status != "needs_reconcile":
                    repository.mark_participant_reconcile(
                        participant.id,
                        updated_at=completed_at,
                    )
                    state_changed = True
            if state_changed:
                repository.record_resource_state_changed(
                    discussion.id,
                    updated_at=completed_at,
                )
        return closed, state_changed

    async def stop(self) -> None:
        """应用退出时先禁止新轮，再把未完成轮保留为可自动恢复状态。"""

        self._accepting = False
        discussion_ids = tuple(self._tasks)
        for discussion_id in discussion_ids:
            active = tuple(self._active_sessions.get(discussion_id, ()))
            for session_id in active:
                try:
                    await self._sessions.interrupt(session_id)
                except Exception:  # noqa: BLE001 - 退出继续，状态留待重启对账。
                    pass
            task = self._tasks.get(discussion_id)
            if task is not None and not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)
        for discussion_id in discussion_ids:
            with self._open_repository() as repository, repository.transaction():
                try:
                    discussion = repository.get_discussion(discussion_id)
                except Exception:  # noqa: BLE001 - 删除并发不阻断应用退出。
                    continue
                if discussion.status != "running":
                    continue
                repository.mark_running_attempts_host_lost(
                    discussion_id,
                    completed_at=self._clock(),
                )
                repository.mark_needs_reconcile(
                    discussion_id,
                    updated_at=self._clock(),
                )
            self._events.publish(discussion_id)

    async def _run_discussion(
        self,
        discussion_id: str,
        *,
        recovering: bool,
    ) -> None:
        """运行当前轮，并按自动模式在共同发布后继续创建下一轮。

        Args:
            discussion_id: 所属研讨 ID。
            recovering: 是否从应用重启留下的 attempt 恢复。
        """

        if recovering:
            with self._open_repository() as repository:
                recoverable = repository.get_discussion(discussion_id)
            if recoverable.active_round_number is None:
                await self.ensure_participants(discussion_id)
                with self._open_repository() as repository, repository.transaction():
                    repository.restore_draft_after_provisioning(
                        discussion_id,
                        updated_at=self._clock(),
                    )
                self._events.publish(discussion_id)
                return
            with self._open_repository() as repository, repository.transaction():
                repository.resume_existing_round(
                    discussion_id,
                    updated_at=self._clock(),
                )
            self._events.publish(discussion_id)
        await self.ensure_participants(discussion_id)
        retry_unfinished = recovering
        while self._accepting:
            with self._open_repository() as repository:
                discussion = repository.get_discussion(discussion_id)
            if discussion.status != "running" or discussion.active_round_number is None:
                return
            round_record = next(
                (
                    item
                    for item in discussion.rounds
                    if item.number == discussion.active_round_number
                ),
                None,
            )
            if round_record is None:
                return
            if round_record.status == "published":
                self._rebuild_transcript(discussion)
                if should_automatically_continue(discussion, round_record):
                    self._create_automatic_round(discussion)
                    retry_unfinished = False
                    continue
                return
            targets = self._targets_for_round(
                round_record,
                recovering=retry_unfinished,
            )
            if targets:
                async with asyncio.TaskGroup() as workers:
                    for participant in discussion.participants:
                        if participant.id not in targets:
                            continue
                        workers.create_task(
                            self._run_participant(
                                discussion,
                                round_record,
                                participant,
                            )
                        )
            with self._open_repository() as repository:
                latest = repository.get_discussion(discussion_id)
            latest_round = next(
                item for item in latest.rounds if item.id == round_record.id
            )
            if not round_ready_to_publish(latest_round):
                return
            publication = self._artifacts.write_publication(latest, latest_round)
            next_status = status_after_publication(latest, latest_round)
            with self._open_repository() as repository, repository.transaction():
                version = repository.publish_round(
                    discussion_id,
                    latest_round.id,
                    publication_artifact=publication.relative_path,
                    publication_sha256=publication.sha256,
                    publication_bytes=publication.byte_count,
                    published_at=self._clock(),
                    next_status=next_status,
                )
            if version is None:
                return
            with self._open_repository() as repository:
                published_discussion = repository.get_discussion(discussion_id)
            self._rebuild_transcript(published_discussion)
            self._events.publish(discussion_id)
            published_round = next(
                item
                for item in published_discussion.rounds
                if item.id == latest_round.id
            )
            if not should_automatically_continue(
                published_discussion,
                published_round,
            ):
                if published_discussion.status == "completed":
                    closed = await self._close_idle_participant_sessions(discussion_id)
                    if closed:
                        self.finalize_episode(discussion_id)
                return
            self._create_automatic_round(published_discussion)
            retry_unfinished = False

    def _create_automatic_round(self, discussion: Discussion) -> None:
        """从刚公开快照创建自动模式下一轮，不等待用户输入。

        Args:
            discussion: 刚完成共同发布的最新研讨聚合。
        """

        round_number = (discussion.active_round_number or 0) + 1
        try:
            prompt = build_round_prompt(
                discussion,
                round_number=round_number,
                kind="regular",
                artifacts=self._artifacts,
            )
        except (OSError, UnicodeError, ValueError):
            self._record_integrity_issue(discussion.id)
            raise
        input_ref = self._artifacts.write_round_input(
            discussion_id=discussion.id,
            round_number=round_number,
            kind="regular",
            prompt=prompt.text,
        )
        record = DiscussionRound(
            id=hashlib.sha256(
                f"round\x1f{discussion.id}\x1f{round_number}".encode("utf-8")
            ).hexdigest()[:32],
            discussion_id=discussion.id,
            number=round_number,
            kind="regular",
            status="running",
            public_context_hash=prompt.sha256,
            input_artifact=input_ref.relative_path,
            input_bytes=input_ref.byte_count,
            publication_artifact=None,
            publication_sha256=None,
            publication_bytes=None,
            started_at=self._clock(),
            published_at=None,
            stop_reason=None,
        )
        with self._open_repository() as repository, repository.transaction():
            repository.create_round(
                discussion.id,
                record,
                [item.id for item in discussion.participants],
                expected_version=discussion.version,
                updated_at=self._clock(),
            )
        self._events.publish(discussion.id)

    async def _run_participant(
        self,
        discussion: Discussion,
        round_record: DiscussionRound,
        participant: DiscussionParticipant,
    ) -> None:
        """在物理 semaphore 内完成一个 participant attempt 并持久封口。

        Args:
            discussion: 当前轮冻结时的研讨聚合。
            round_record: 当前逻辑轮。
            participant: 本次占用的稳定 participant 槽位。
        """

        async with self._semaphore:
            with self._open_repository() as repository:
                latest = repository.get_discussion(discussion.id)
            current_participant = next(
                item for item in latest.participants if item.id == participant.id
            )
            session = await self._sessions.ensure_session(
                current_participant,
                workdir=discussion.workdir,
            )
            with self._open_repository() as repository, repository.transaction():
                repository.bind_participant_session(
                    participant.id,
                    agent_session_id=session.agent_session_id,
                    native_session_id=session.native_session_id,
                    updated_at=self._clock(),
                )
                ordinal = repository.next_attempt_ordinal(
                    round_record.id,
                    participant.id,
                )
            attempt_id = uuid.uuid4().hex
            event_ref = self._artifacts.initialize_attempt_events(
                discussion_id=discussion.id,
                round_number=round_record.number,
                participant_id=participant.id,
                ordinal=ordinal,
                attempt_id=attempt_id,
            )
            dispatch_key = (
                f"discussion:{discussion.id}:round:{round_record.number}:"
                f"participant:{participant.id}:attempt:{ordinal}"
            )
            with self._open_repository() as repository, repository.transaction():
                actual_ordinal = repository.start_attempt(
                    attempt_id=attempt_id,
                    round_id=round_record.id,
                    participant_id=participant.id,
                    input_hash=round_record.public_context_hash,
                    event_artifact=event_ref.relative_path,
                    event_sha256=event_ref.sha256,
                    event_bytes=event_ref.byte_count,
                    dispatch_key=dispatch_key,
                    started_at=self._clock(),
                )
            if actual_ordinal != ordinal:
                raise RuntimeError("discussion attempt ordinal changed during dispatch")
            self._active_sessions.setdefault(discussion.id, set()).add(
                session.agent_session_id
            )
            try:
                try:
                    prompt = self._artifacts.read_text(
                        round_record.input_artifact,
                        expected_sha256=round_record.public_context_hash,
                        expected_bytes=round_record.input_bytes,
                    )
                except (OSError, UnicodeError, ValueError):
                    self._record_integrity_issue(discussion.id)
                    raise
                outcome = await self._consume_turn(
                    participant,
                    session.agent_session_id,
                    prompt,
                    attempt_id=attempt_id,
                )
            except asyncio.CancelledError:
                try:
                    await self._sessions.interrupt(session.agent_session_id)
                except Exception:  # noqa: BLE001 - 取消仍要等待 worker 退出并进入统一对账。
                    pass
                raise
            except DiscussionRuntimeError:
                outcome = _TurnOutcome(
                    status="host_lost",
                    text="",
                    root_turn_id=None,
                    usage=None,
                    activity=_empty_activity(),
                    error_code="HOST_LOST",
                    error_message="参与者运行工具连接异常结束",
                    events=({"type": "host_lost"},),
                )
            except Exception:
                try:
                    await self._sessions.interrupt(session.agent_session_id)
                except Exception:  # noqa: BLE001 - 原始本地故障必须继续触发 reconcile。
                    pass
                raise
            finally:
                self._active_sessions.get(discussion.id, set()).discard(
                    session.agent_session_id
                )
            events_ref = self._artifacts.write_attempt_events(
                discussion_id=discussion.id,
                round_number=round_record.number,
                participant_id=participant.id,
                ordinal=ordinal,
                attempt_id=attempt_id,
                events=outcome.events,
            )
            output_ref = None
            if outcome.status == "succeeded":
                output_ref = self._artifacts.write_attempt_output(
                    discussion_id=discussion.id,
                    round_number=round_record.number,
                    participant_id=participant.id,
                    ordinal=ordinal,
                    attempt_id=attempt_id,
                    text=outcome.text,
                )
            binding = self._sessions.binding(session.agent_session_id)
            with self._open_repository() as repository, repository.transaction():
                repository.update_attempt_event_artifact(
                    attempt_id,
                    event_sha256=events_ref.sha256,
                    event_bytes=events_ref.byte_count,
                )
                if outcome.root_turn_id is not None:
                    repository.set_attempt_root_turn(
                        attempt_id,
                        outcome.root_turn_id,
                    )
                repository.complete_attempt(
                    attempt_id=attempt_id,
                    status=outcome.status,
                    completed_at=self._clock(),
                    output_artifact=(
                        output_ref.relative_path if output_ref is not None else None
                    ),
                    output_sha256=(
                        output_ref.sha256 if output_ref is not None else None
                    ),
                    output_bytes=(
                        output_ref.byte_count if output_ref is not None else None
                    ),
                    error_code=outcome.error_code,
                    error_message=outcome.error_message,
                    usage_json=(
                        json.dumps(outcome.usage, ensure_ascii=False, sort_keys=True)
                        if outcome.usage is not None
                        else None
                    ),
                    activity_json=json.dumps(
                        outcome.activity,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                if binding is not None and binding.native_session_id is not None:
                    effective_model = participant.effective_model
                    if binding.model is not None and (
                        binding.model != binding.requested_model
                        or participant.effective_model == participant.model
                    ):
                        effective_model = binding.model
                    repository.update_participant_native_session(
                        participant.id,
                        binding.native_session_id,
                        effective_model=effective_model,
                        updated_at=self._clock(),
                    )

    def _rebuild_transcript(self, discussion: Discussion) -> None:
        """重建派生记录，并把任何 artifact 读取漂移登记为 durable outbox。

        Args:
            discussion: 要从 SQLite 公开事实重建 transcript 的研讨。
        """

        try:
            self._artifacts.rebuild_transcript(discussion)
        except (OSError, UnicodeError, ValueError):
            self._record_integrity_issue(discussion.id)
            raise

    def _record_integrity_issue(self, discussion_id: str) -> None:
        """幂等登记 coordinator 路径发现的 artifact 完整性故障。

        Args:
            discussion_id: 需要重新验真的研讨 ID。
        """

        with self._open_repository() as repository, repository.transaction():
            repository.record_integrity_issue(
                discussion_id,
                detected_at=self._clock(),
            )

    async def _consume_turn(
        self,
        participant: DiscussionParticipant,
        agent_session_id: str,
        prompt: str,
        *,
        attempt_id: str,
    ) -> _TurnOutcome:
        """只接受当前根 turn 的终态，并始终把运行流读取到自然 EOF。

        Args:
            participant: 当前 participant，用于 runtime 根线程核对。
            agent_session_id: 当前 Trowel 会话 ID。
            prompt: 与 input artifact 完全一致的普通输入。
            attempt_id: 当前 durable attempt ID，用于及时保存 runtime 接受事实。

        Returns:
            不含工具正文的稳定 attempt 结果。
        """

        root_turn_id: str | None = None
        text_parts: list[str] = []
        usage: dict[str, Any] | None = None
        tool_calls: dict[str, str] = {}
        subagent_ids: set[str] = set()
        audit_events: list[dict[str, Any]] = []
        terminal: tuple[str, str | None, str | None] | None = None
        stream = self._sessions.run_turn(agent_session_id, prompt)
        try:
            async for event in stream:
                if event.get("session_id") != agent_session_id:
                    continue
                event_type = event.get("type")
                event_turn_id = event.get("turn_id")
                if event_type == "turn_start" and isinstance(event_turn_id, str):
                    if participant.runtime is Runtime.CODEX:
                        binding = self._sessions.binding(agent_session_id)
                        native = (
                            binding.native_session_id if binding is not None else None
                        )
                        thread_id = event.get("thread_id")
                        if native is not None and thread_id != native:
                            continue
                    if root_turn_id is None:
                        root_turn_id = event_turn_id
                        with (
                            self._open_repository() as repository,
                            repository.transaction(),
                        ):
                            repository.mark_attempt_accepted(attempt_id)
                            repository.set_attempt_root_turn(attempt_id, root_turn_id)
                        audit_events.append(
                            {"type": "turn_start", "turn_id": root_turn_id}
                        )
                    continue
                matches_root = (
                    root_turn_id is not None
                    and isinstance(event_turn_id, str)
                    and event_turn_id == root_turn_id
                )
                startup_error = (
                    root_turn_id is None
                    and event_type == "error"
                    and event_turn_id is None
                )
                if event_type == "tool_call" and matches_root:
                    payload = event.get("payload")
                    tool_name = (
                        payload.get("tool_name")
                        if isinstance(payload, dict)
                        else None
                    )
                    item_id = event.get("item_id")
                    if isinstance(tool_name, str) and tool_name:
                        stable_id = (
                            item_id
                            if isinstance(item_id, str) and item_id
                            else f"seq:{event.get('seq')}"
                        )
                        tool_calls.setdefault(stable_id, tool_name)
                        if tool_name == "Agent":
                            subagent_ids.add(stable_id)
                    continue
                if (
                    event_type in {"subagent_progress", "subagent_activity"}
                    and matches_root
                ):
                    payload = event.get("payload")
                    if isinstance(payload, dict):
                        subagent_id = payload.get("task_id") or payload.get(
                            "agent_thread_id"
                        )
                        if isinstance(subagent_id, str) and subagent_id:
                            subagent_ids.add(subagent_id)
                    continue
                if event_type == "elicit_request" and matches_root:
                    try:
                        cancelled = await self._sessions.cancel_elicitation(
                            agent_session_id
                        )
                    except DiscussionRuntimeError:
                        cancelled = False
                    audit_events.append(
                        {
                            "type": "elicit_request",
                            "action": "denied" if cancelled else "cancel_failed",
                        }
                    )
                    if not cancelled:
                        try:
                            await self._sessions.interrupt(agent_session_id)
                        except Exception:  # noqa: BLE001 - attempt 已有明确失败终态。
                            pass
                        terminal = (
                            "failed",
                            "ELICITATION_CANCEL_FAILED",
                            "参与者请求了无法在研讨中回答的交互问题",
                        )
                        continue
                    continue
                if event_type == "approval_request" and matches_root:
                    payload = event.get("payload")
                    approval_status = (
                        payload.get("status") if isinstance(payload, dict) else None
                    )
                    if approval_status != "pending":
                        audit_events.append(
                            {
                                "type": "approval_request",
                                "action": "resolution_observed",
                            }
                        )
                        continue
                    request_id = (
                        payload.get("request_id") if isinstance(payload, dict) else None
                    )
                    try:
                        if not isinstance(request_id, str) or not request_id:
                            raise DiscussionRuntimeError("审批请求缺少身份")
                        self._sessions.decline_approval(
                            agent_session_id,
                            request_id,
                        )
                    except DiscussionRuntimeError:
                        try:
                            await self._sessions.interrupt(agent_session_id)
                        except Exception:  # noqa: BLE001 - attempt 已有明确失败终态。
                            pass
                        terminal = (
                            "failed",
                            "APPROVAL_DECLINE_FAILED",
                            "参与者请求了无法在研讨中批准的操作",
                        )
                        audit_events.append(
                            {"type": "approval_request", "action": "decline_failed"}
                        )
                        continue
                    audit_events.append(
                        {"type": "approval_request", "action": "declined"}
                    )
                    continue
                if event_type == "text" and matches_root:
                    if terminal is not None:
                        continue
                    payload = event.get("payload")
                    text = payload.get("text") if isinstance(payload, dict) else None
                    if isinstance(text, str):
                        text_parts.append(text)
                    continue
                if event_type in {"usage_updated", "context_usage"} and matches_root:
                    payload = event.get("payload")
                    if isinstance(payload, dict):
                        usage = _safe_usage(payload)
                    continue
                if event_type == "finished" and matches_root:
                    if terminal is None:
                        terminal = ("succeeded", None, None)
                        audit_events.append(
                            {"type": "finished", "turn_id": root_turn_id}
                        )
                    continue
                if event_type == "interrupted" and matches_root:
                    if terminal is None:
                        terminal = (
                            "interrupted",
                            "TURN_INTERRUPTED",
                            "参与者回答被中断",
                        )
                        audit_events.append(
                            {"type": "interrupted", "turn_id": root_turn_id}
                        )
                    continue
                if event_type == "error" and (matches_root or startup_error):
                    if terminal is None:
                        status, code, message = _classify_terminal_error(event)
                        terminal = (status, code, message)
                        audit_events.append(
                            {
                                "type": "error",
                                "turn_id": root_turn_id,
                                "code": code,
                            }
                        )
                    continue
                if event_type == "session_exited" and terminal is None:
                    terminal = (
                        "host_lost",
                        "HOST_EXITED",
                        "参与者运行工具在回答完成前退出",
                    )
                    audit_events.append({"type": "host_exited"})
                    continue
                if event_type == "host_status" and terminal is None:
                    payload = event.get("payload")
                    host_status = (
                        payload.get("status") if isinstance(payload, dict) else None
                    )
                    host_kind = (
                        payload.get("kind") if isinstance(payload, dict) else None
                    )
                    if host_status in {
                        "exited",
                        "host_exited",
                        "failed",
                    } or host_kind in {
                        "exited",
                        "host_exited",
                    }:
                        terminal = (
                            "host_lost",
                            "HOST_EXITED",
                            "参与者运行工具在回答完成前退出",
                        )
                        audit_events.append({"type": "host_exited"})
                        continue
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        if terminal is None:
            return _TurnOutcome(
                status="host_lost",
                text="",
                root_turn_id=root_turn_id,
                usage=usage,
                activity=_summarize_activity(tool_calls, subagent_ids),
                error_code="STREAM_ENDED_WITHOUT_TERMINAL",
                error_message="参与者事件流结束，但没有收到完成或错误终态",
                events=tuple(audit_events + [{"type": "stream_ended"}]),
            )
        status, error_code, error_message = terminal
        answer = "".join(text_parts).strip()
        if status == "succeeded" and not answer:
            status = "failed"
            error_code = "EMPTY_ANSWER"
            error_message = "参与者已结束，但没有形成可公开的文字回答"
        return _TurnOutcome(
            status=status,
            text=answer,
            root_turn_id=root_turn_id,
            usage=usage,
            activity=_summarize_activity(tool_calls, subagent_ids),
            error_code=error_code,
            error_message=error_message,
            events=tuple(audit_events),
        )

    @staticmethod
    def _targets_for_round(
        round_record: DiscussionRound,
        *,
        recovering: bool,
    ) -> frozenset[str]:
        """选择本次 worker 要处理的槽位，永不重跑已成功 participant。

        Args:
            round_record: 当前逻辑轮。
            recovering: 是否允许重试应用退出留下的未完成槽位。

        Returns:
            要启动新 attempt 的 participant ID 集合。
        """

        allowed = {"pending"}
        if recovering:
            allowed.update({"running", "host_lost", "needs_reconcile"})
        return frozenset(
            item.participant_id
            for item in round_record.results
            if item.status in allowed
        )


class _TurnOutcome:
    """保存一个 attempt 可持久化且不含工具正文的归一化结果。"""

    def __init__(
        self,
        *,
        status: str,
        text: str,
        root_turn_id: str | None,
        usage: dict[str, Any] | None,
        activity: dict[str, Any],
        error_code: str | None,
        error_message: str | None,
        events: tuple[dict[str, Any], ...],
    ) -> None:
        """创建归一化 turn 结果。

        Args:
            status: succeeded 或稳定失败分类。
            text: 只有 succeeded 会持久化的完整可见回答。
            root_turn_id: 本次真正根 turn ID。
            usage: 去正文用量摘要。
            activity: 去敏后的工具调用和子 Agent 统计。
            error_code: 稳定失败代码。
            error_message: 脱敏失败说明。
            events: 不含 text/tool payload 的审计事件。
        """

        self.status = status
        self.text = text
        self.root_turn_id = root_turn_id
        self.usage = usage
        self.activity = activity
        self.error_code = error_code
        self.error_message = error_message
        self.events = events


def _empty_activity() -> dict[str, Any]:
    """返回没有工具活动时仍保持稳定 shape 的摘要。"""

    return {"tool_call_count": 0, "tool_names": {}, "subagent_count": 0}


def _summarize_activity(
    tool_calls: dict[str, str],
    subagent_ids: set[str],
) -> dict[str, Any]:
    """把去重后的调用身份压缩成不含参数和正文的公开统计。

    Args:
        tool_calls: 稳定调用 ID 到工具名的映射。
        subagent_ids: 本轮启动或观测到的稳定子 Agent 身份。

    Returns:
        总调用数、按工具名计数和子 Agent 数量。
    """

    names: dict[str, int] = {}
    for name in tool_calls.values():
        names[name] = names.get(name, 0) + 1
    return {
        "tool_call_count": len(tool_calls),
        "tool_names": dict(sorted(names.items())),
        "subagent_count": len(subagent_ids),
    }


def _safe_usage(payload: dict[str, Any]) -> dict[str, Any]:
    """只保留数值或短状态字段，避免 usage 旁路携带正文。

    Args:
        payload: AgentEvent 的 usage payload。

    Returns:
        只含标量的浅层用量摘要。
    """

    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, (int, float, bool))
        or (isinstance(value, str) and len(value) <= 80)
    }


def _classify_terminal_error(event: dict[str, Any]) -> tuple[str, str, str]:
    """把 runtime error payload 分类后立即丢弃其原始文本。

    Args:
        event: 当前根 turn 的 error AgentEvent。

    Returns:
        participant status、稳定错误码和脱敏说明。
    """

    payload = event.get("payload")
    raw = json.dumps(payload, ensure_ascii=False).lower()
    if "turn_in_progress" in raw:
        return (
            "failed",
            "TURN_IN_PROGRESS",
            "参与者上一轮仍在收尾，本轮输入没有发送",
        )
    if any(token in raw for token in ("429", "quota", "rate limit", "usage limit")):
        return "limited", "USAGE_LIMITED", "参与者连接达到额度或频率限制"
    if "timeout" in raw or "timed out" in raw:
        return "timed_out", "RUNTIME_TIMEOUT", "参与者运行工具请求超时"
    return "failed", "RUNTIME_ERROR", "参与者运行工具返回错误终态"
