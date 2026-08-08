"""以 SQLite 保存研讨控制面、版本和原子发布事实。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trowel_py.agent_host.binding import Runtime
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations
from trowel_py.discussion.errors import (
    DiscussionCommandConflictError,
    DiscussionNotFoundError,
    DiscussionStateError,
    DiscussionVersionConflictError,
)
from trowel_py.discussion.models import (
    Discussion,
    DiscussionParticipant,
    DiscussionRound,
    ParticipantAttemptHistoryRequest,
    ParticipantResult,
    UserMessage,
)


class DiscussionRepository:
    """提供单连接上的研讨查询和短事务写入操作。

    Attributes:
        connection: 已启用外键的主 SQLite 连接。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """绑定一条由调用方管理生命周期的 SQLite 连接。

        Args:
            connection: 使用 ``sqlite3.Row`` 的主库连接。
        """

        self.connection = connection

    @contextmanager
    def transaction(self) -> Iterator[DiscussionRepository]:
        """用 ``BEGIN IMMEDIATE`` 串行化一次短写事务。

        Yields:
            当前 repository，供调用方在同一事务中执行多个写步骤。
        """

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def insert_discussion(
        self,
        discussion: Discussion,
        participants: Sequence[DiscussionParticipant],
        initial_message: UserMessage,
    ) -> None:
        """在同一事务中写入研讨、冻结参与者和初始用户原话。

        Args:
            discussion: 尚无轮次的初始研讨记录。
            participants: 已按 position 排序且至少两位的冻结参与者。
            initial_message: sequence=1、after_round_number=0 的初始议题来源。
        """

        self.connection.execute(
            """
            INSERT INTO discussions(
                id, create_request_id, create_request_hash, topic, workdir,
                progression_mode, max_rounds, status, version,
                active_round_number, episode_id, created_at, updated_at,
                completed_at, stopped_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                discussion.id,
                discussion.create_request_id,
                discussion.create_request_hash,
                discussion.topic,
                discussion.workdir,
                discussion.progression_mode,
                discussion.max_rounds,
                discussion.status,
                discussion.version,
                discussion.active_round_number,
                discussion.episode_id,
                discussion.created_at,
                discussion.updated_at,
                discussion.completed_at,
                discussion.stopped_at,
                discussion.deleted_at,
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO discussion_participants(
                id, discussion_id, position, name, runtime, connection_id,
                connection_name, model, effective_model,
                effort, session_configuration_id, connection_identity_version,
                permission_mode, permission_preset,
                memory_enabled, profile_enabled, self_enabled,
                owner_ref, agent_session_id, native_session_id, status,
                capability_version, capability_source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.id,
                    item.discussion_id,
                    item.position,
                    item.name,
                    item.runtime.value,
                    item.connection_id,
                    item.connection_name,
                    item.model,
                    item.effective_model,
                    item.effort,
                    item.session_configuration_id,
                    item.connection_identity_version,
                    item.permission_mode,
                    item.permission_preset,
                    int(item.memory_enabled),
                    int(item.profile_enabled),
                    int(item.self_enabled),
                    item.owner_ref,
                    item.agent_session_id,
                    item.native_session_id,
                    item.status,
                    item.capability_version,
                    item.capability_source,
                    item.created_at,
                    item.updated_at,
                )
                for item in participants
            ],
        )
        self._insert_user_message(initial_message)
        self.append_event(
            discussion.id,
            "discussion_created",
            discussion.version,
            created_at=discussion.created_at,
        )

    def get_discussion(
        self,
        discussion_id: str,
        *,
        include_deleted: bool = False,
    ) -> Discussion:
        """读取一场研讨及其全部稳定子记录。

        Args:
            discussion_id: 要查询的研讨 ID。
            include_deleted: 是否允许返回软删除记录。

        Returns:
            参与者、消息和轮次都按冻结顺序排列的聚合对象。

        Raises:
            DiscussionNotFoundError: 记录不存在或已删除且未要求包含。
        """

        row = self.connection.execute(
            "SELECT * FROM discussions WHERE id=?",
            (discussion_id,),
        ).fetchone()
        if row is None or (row["status"] == "deleted" and not include_deleted):
            raise DiscussionNotFoundError()
        participants = tuple(
            self._participant_from_row(item)
            for item in self.connection.execute(
                """
                SELECT * FROM discussion_participants
                WHERE discussion_id=? ORDER BY position
                """,
                (discussion_id,),
            ).fetchall()
        )
        messages = tuple(
            self._message_from_row(item)
            for item in self.connection.execute(
                """
                SELECT * FROM discussion_user_messages
                WHERE discussion_id=? ORDER BY sequence
                """,
                (discussion_id,),
            ).fetchall()
        )
        rounds = tuple(
            self._round_from_row(item)
            for item in self.connection.execute(
                """
                SELECT * FROM discussion_rounds
                WHERE discussion_id=? ORDER BY number
                """,
                (discussion_id,),
            ).fetchall()
        )
        return Discussion(
            id=str(row["id"]),
            create_request_id=str(row["create_request_id"]),
            create_request_hash=str(row["create_request_hash"]),
            topic=str(row["topic"]),
            workdir=str(row["workdir"]),
            progression_mode=row["progression_mode"],
            max_rounds=(int(row["max_rounds"]) if row["max_rounds"] else None),
            status=row["status"],
            version=int(row["version"]),
            active_round_number=(
                int(row["active_round_number"])
                if row["active_round_number"] is not None
                else None
            ),
            episode_id=(str(row["episode_id"]) if row["episode_id"] else None),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=(str(row["completed_at"]) if row["completed_at"] else None),
            stopped_at=str(row["stopped_at"]) if row["stopped_at"] else None,
            deleted_at=str(row["deleted_at"]) if row["deleted_at"] else None,
            participants=participants,
            messages=messages,
            rounds=rounds,
        )

    def get_discussion_by_create_request(self, request_id: str) -> Discussion | None:
        """按创建请求身份读取已经提交的研讨。

        Args:
            request_id: 创建请求的客户端稳定身份。

        Returns:
            已提交研讨；首次请求为 None。
        """

        row = self.connection.execute(
            "SELECT id FROM discussions WHERE create_request_id=?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        return self.get_discussion(str(row["id"]), include_deleted=True)

    def list_discussions(self, *, limit: int = 100) -> tuple[Discussion, ...]:
        """返回最近更新且尚未删除的研讨摘要聚合。

        Args:
            limit: 最多返回的记录数。

        Returns:
            按更新时间倒序排列的完整研讨对象。
        """

        ids = [
            str(row["id"])
            for row in self.connection.execute(
                """
                SELECT id FROM discussions
                WHERE status != 'deleted'
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        return tuple(self.get_discussion(item) for item in ids)

    def participant_by_owner_ref(self, owner_ref: str) -> DiscussionParticipant | None:
        """按持久 owner_ref 查找 participant，用于跨存储 saga 对账。

        Args:
            owner_ref: binding 与 participant 共享的稳定归属键。

        Returns:
            匹配的参与者；不存在时为 None。
        """

        row = self.connection.execute(
            "SELECT * FROM discussion_participants WHERE owner_ref=?",
            (owner_ref,),
        ).fetchone()
        return self._participant_from_row(row) if row is not None else None

    def get_attempt_history_request(
        self,
        discussion_id: str,
        attempt_id: str,
    ) -> ParticipantAttemptHistoryRequest:
        """读取原生历史定位需要的 attempt 与 participant 联接事实。

        Args:
            discussion_id: URL 中已经授权查看的研讨 ID。
            attempt_id: 要恢复单轮轨迹的 attempt ID。

        Returns:
            不含 prompt 正文、工具正文和连接凭据的历史定位请求。

        Raises:
            DiscussionNotFoundError: attempt 不存在或属于另一场研讨。
        """

        row = self.connection.execute(
            """
            SELECT a.id, a.input_hash, a.root_turn_id, a.status,
                   r.discussion_id, r.number AS round_number,
                   p.id AS participant_id, p.runtime, p.agent_session_id,
                   p.native_session_id, d.workdir,
                   1 + (
                       SELECT COUNT(*)
                       FROM discussion_attempts previous
                       JOIN discussion_rounds previous_round
                         ON previous_round.id=previous.round_id
                       WHERE previous.participant_id=a.participant_id
                         AND previous.input_hash=a.input_hash
                         AND previous.root_turn_id IS NOT NULL
                         AND (
                           previous_round.number < r.number
                           OR (
                             previous_round.number = r.number
                             AND previous.ordinal < a.ordinal
                           )
                         )
                   ) AS input_occurrence
            FROM discussion_attempts a
            JOIN discussion_rounds r ON r.id=a.round_id
            JOIN discussions d ON d.id=r.discussion_id
            JOIN discussion_participants p ON p.id=a.participant_id
            WHERE a.id=? AND r.discussion_id=? AND d.status!='deleted'
            """,
            (attempt_id, discussion_id),
        ).fetchone()
        if row is None:
            raise DiscussionNotFoundError()
        return ParticipantAttemptHistoryRequest(
            id=str(row["id"]),
            discussion_id=str(row["discussion_id"]),
            round_number=int(row["round_number"]),
            participant_id=str(row["participant_id"]),
            runtime=Runtime(str(row["runtime"])),
            agent_session_id=(
                str(row["agent_session_id"]) if row["agent_session_id"] else None
            ),
            native_session_id=(
                str(row["native_session_id"]) if row["native_session_id"] else None
            ),
            workdir=str(row["workdir"]),
            input_hash=str(row["input_hash"]),
            input_occurrence=int(row["input_occurrence"]),
            root_turn_id=(
                str(row["root_turn_id"]) if row["root_turn_id"] else None
            ),
            status=str(row["status"]),
        )

    def bind_participant_session(
        self,
        participant_id: str,
        *,
        agent_session_id: str,
        native_session_id: str | None,
        updated_at: str,
    ) -> None:
        """把已创建的 Session Hub binding 幂等认领给 participant。

        Args:
            participant_id: 要更新的参与者 ID。
            agent_session_id: 当前 Trowel 会话 ID。
            native_session_id: 当前已知的原生会话 ID。
            updated_at: 本次更新时间。
        """

        self.connection.execute(
            """
            UPDATE discussion_participants
            SET agent_session_id=?, native_session_id=COALESCE(?, native_session_id),
                status='ready', updated_at=?
            WHERE id=?
            """,
            (
                agent_session_id,
                native_session_id,
                updated_at,
                participant_id,
            ),
        )

    def restore_draft_after_provisioning(
        self,
        discussion_id: str,
        *,
        updated_at: str,
    ) -> int:
        """参与者会话补齐后把尚无轮次的待对账议题恢复为 draft。

        Args:
            discussion_id: 创建阶段曾 provision 失败的研讨 ID。
            updated_at: 恢复完成时间。

        Returns:
            恢复后的新版本。
        """

        row = self.connection.execute(
            "SELECT version, status, active_round_number FROM discussions WHERE id=?",
            (discussion_id,),
        ).fetchone()
        if row is None:
            raise DiscussionNotFoundError()
        if row["status"] != "needs_reconcile" or row["active_round_number"] is not None:
            raise DiscussionStateError("只有首轮前的待对账研讨可以恢复为草稿")
        rounds = self.connection.execute(
            "SELECT COUNT(*) AS total FROM discussion_rounds WHERE discussion_id=?",
            (discussion_id,),
        ).fetchone()
        if int(rounds["total"]) != 0:
            raise DiscussionStateError("已有轮次的研讨不能恢复为草稿")
        version = int(row["version"]) + 1
        self.connection.execute(
            """
            UPDATE discussions SET status='draft', version=?, updated_at=?
            WHERE id=? AND status='needs_reconcile'
            """,
            (version, updated_at, discussion_id),
        )
        self.append_event(
            discussion_id,
            "discussion_ready",
            version,
            created_at=updated_at,
        )
        return version

    def update_participant_native_session(
        self,
        participant_id: str,
        native_session_id: str,
        *,
        effective_model: str,
        updated_at: str,
    ) -> None:
        """在原生 host 回报身份后补齐恢复引用和实际模型。

        Args:
            participant_id: 所属参与者 ID。
            native_session_id: Claude session ID 或 Codex thread ID。
            effective_model: runtime 已确认或创建时映射的实际模型 ID。
            updated_at: 本次更新时间。
        """

        self.connection.execute(
            """
            UPDATE discussion_participants
            SET native_session_id=?, effective_model=?, updated_at=? WHERE id=?
            """,
            (native_session_id, effective_model, updated_at, participant_id),
        )

    def mark_participant_reconcile(
        self, participant_id: str, *, updated_at: str
    ) -> None:
        """把无法确认关闭或恢复结果的 participant 标成待对账。

        Args:
            participant_id: 所属参与者 ID。
            updated_at: 本次更新时间。
        """

        self.connection.execute(
            """
            UPDATE discussion_participants
            SET status='needs_reconcile', updated_at=? WHERE id=?
            """,
            (updated_at, participant_id),
        )

    def add_user_message(
        self,
        discussion_id: str,
        message: UserMessage,
        *,
        expected_version: int,
        updated_at: str,
    ) -> int:
        """在等待用户状态下保存补充，并以 expected version 抢占写权限。

        Args:
            discussion_id: 所属研讨 ID。
            message: 已带下一 sequence 的顶层用户原话。
            expected_version: 调用方看到的研讨版本。
            updated_at: 本次命令时间。

        Returns:
            写入后的新研讨版本。
        """

        version = self.advance_version(
            discussion_id,
            expected_version=expected_version,
            allowed_statuses=("waiting_user",),
            updated_at=updated_at,
        )
        self._insert_user_message(message)
        self.append_event(
            discussion_id,
            "user_message_added",
            version,
            round_number=message.after_round_number,
            created_at=updated_at,
        )
        return version

    def next_message_sequence(self, discussion_id: str) -> int:
        """返回指定研讨下一条顶层用户消息序号。

        Args:
            discussion_id: 所属研讨 ID。

        Returns:
            当前最大 sequence 加一。
        """

        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM discussion_user_messages WHERE discussion_id=?
            """,
            (discussion_id,),
        ).fetchone()
        return int(row["next_sequence"])

    def create_round(
        self,
        discussion_id: str,
        round_record: DiscussionRound,
        participant_ids: Sequence[str],
        *,
        expected_version: int | None,
        updated_at: str,
        progression_mode: str | None = None,
        max_rounds: int | None = None,
    ) -> int:
        """原子冻结一轮及全部稳定槽位，并把研讨切到 running。

        Args:
            discussion_id: 所属研讨 ID。
            round_record: 已先持久写好公共输入 artifact 的轮次记录。
            participant_ids: 按冻结 position 排列的完整参与者 ID。
            expected_version: 用户命令版本；自动推进传 None。
            updated_at: 本次状态变化时间。
            progression_mode: 用户在公开边界选择的新推进方式。
            max_rounds: 自动模式新的绝对停止轮号；逐轮模式为 None。

        Returns:
            新研讨版本。
        """

        if expected_version is None:
            current = self.get_discussion(discussion_id)
            expected_version = current.version
        version = self.advance_version(
            discussion_id,
            expected_version=expected_version,
            allowed_statuses=("draft", "waiting_user", "running", "needs_reconcile"),
            updated_at=updated_at,
            status="running",
            active_round_number=round_record.number,
        )
        if progression_mode is not None:
            self.connection.execute(
                """
                UPDATE discussions SET progression_mode=?, max_rounds=?
                WHERE id=? AND version=?
                """,
                (progression_mode, max_rounds, discussion_id, version),
            )
        self.connection.execute(
            """
            INSERT INTO discussion_rounds(
                id, discussion_id, number, kind, status, public_context_hash,
                input_artifact, input_bytes, publication_artifact,
                publication_sha256, publication_bytes, started_at, published_at,
                stop_reason
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, NULL, NULL, NULL, ?, NULL, NULL)
            """,
            (
                round_record.id,
                discussion_id,
                round_record.number,
                round_record.kind,
                round_record.public_context_hash,
                round_record.input_artifact,
                round_record.input_bytes,
                round_record.started_at,
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO discussion_round_participants(
                round_id, participant_id, position, status
            ) VALUES (?, ?, ?, 'pending')
            """,
            [
                (round_record.id, participant_id, position)
                for position, participant_id in enumerate(participant_ids)
            ],
        )
        self.append_event(
            discussion_id,
            "round_started",
            version,
            round_number=round_record.number,
            created_at=updated_at,
        )
        return version

    def start_attempt(
        self,
        *,
        attempt_id: str,
        round_id: str,
        participant_id: str,
        input_hash: str,
        event_artifact: str,
        event_sha256: str,
        event_bytes: int,
        dispatch_key: str,
        started_at: str,
    ) -> int:
        """为未成功槽位创建下一次 durable attempt 并切到 running。

        Args:
            attempt_id: 新尝试稳定 ID。
            round_id: 所属轮次 ID。
            participant_id: 所属参与者 ID。
            input_hash: 必须等于 round 公共输入 hash。
            event_artifact: 仅含去敏事件的相对文件路径。
            event_sha256: 初始空事件文件的 SHA-256。
            event_bytes: 初始事件文件的字节数。
            dispatch_key: 本轮此参与者这次派发的稳定幂等键。
            started_at: 调度开始时间。

        Returns:
            该槽位新的 attempt ordinal。
        """

        ordinal = self.next_attempt_ordinal(round_id, participant_id)
        self.connection.execute(
            """
            INSERT INTO discussion_attempts(
                id, round_id, participant_id, ordinal, status, dispatch_key,
                input_hash, event_artifact, event_sha256, event_bytes, started_at
            ) VALUES (?, ?, ?, ?, 'dispatching', ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                round_id,
                participant_id,
                ordinal,
                dispatch_key,
                input_hash,
                event_artifact,
                event_sha256,
                event_bytes,
                started_at,
            ),
        )
        self.connection.execute(
            """
            UPDATE discussion_round_participants
            SET status='running', current_attempt_id=?, started_at=COALESCE(started_at, ?),
                completed_at=NULL, error_code=NULL, error_message=NULL
            WHERE round_id=? AND participant_id=? AND status != 'succeeded'
            """,
            (attempt_id, started_at, round_id, participant_id),
        )
        return ordinal

    def next_attempt_ordinal(self, round_id: str, participant_id: str) -> int:
        """读取一个 round slot 的下一尝试序号。

        Args:
            round_id: 所属轮次 ID。
            participant_id: 所属参与者 ID。

        Returns:
            当前最大 ordinal 加一。
        """

        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
            FROM discussion_attempts
            WHERE round_id=? AND participant_id=?
            """,
            (round_id, participant_id),
        ).fetchone()
        return int(row["next_ordinal"])

    def update_attempt_event_artifact(
        self,
        attempt_id: str,
        *,
        event_sha256: str,
        event_bytes: int,
    ) -> None:
        """在去敏事件文件原子替换后更新完整性元数据。

        Args:
            attempt_id: 所属尝试 ID。
            event_sha256: 最终 events.jsonl 的 SHA-256。
            event_bytes: 最终 events.jsonl 的字节数。
        """

        self.connection.execute(
            """
            UPDATE discussion_attempts SET event_sha256=?, event_bytes=? WHERE id=?
            """,
            (event_sha256, event_bytes, attempt_id),
        )

    def mark_attempt_accepted(self, attempt_id: str) -> bool:
        """把 runtime 已明确接受的派发从 dispatching 改为 running。

        Args:
            attempt_id: 当前尝试 ID。

        Returns:
            首次确认接受时为 True；已被封口或对账时为 False。
        """

        changed = self.connection.execute(
            """
            UPDATE discussion_attempts SET status='running'
            WHERE id=? AND status='dispatching'
            """,
            (attempt_id,),
        ).rowcount
        return changed == 1

    def set_attempt_root_turn(self, attempt_id: str, root_turn_id: str) -> None:
        """保存当前 attempt 的原生根 turn ID，用于拒绝旧/子 turn 终态。

        Args:
            attempt_id: 当前尝试 ID。
            root_turn_id: 运行工具回报的根 turn ID。
        """

        self.connection.execute(
            """
            UPDATE discussion_attempts SET root_turn_id=?
            WHERE id=? AND status IN ('dispatching', 'running')
            """,
            (root_turn_id, attempt_id),
        )

    def complete_attempt(
        self,
        *,
        attempt_id: str,
        status: str,
        completed_at: str,
        output_artifact: str | None = None,
        output_sha256: str | None = None,
        output_bytes: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        usage_json: str | None = None,
        activity_json: str | None = None,
    ) -> bool:
        """只允许当前 running attempt 首次决定 participant 槽位终态。

        Args:
            attempt_id: 要完成的尝试 ID。
            status: succeeded 或明确失败分类。
            completed_at: 终态时间。
            output_artifact: succeeded 时已 durable 的正文相对路径。
            output_sha256: succeeded 正文的 SHA-256。
            output_bytes: succeeded 正文的 UTF-8 字节数。
            error_code: 稳定失败代码。
            error_message: 脱敏失败说明。
            usage_json: 当前尝试的用量摘要。
            activity_json: 当前尝试去敏后的工具与子 Agent 活动摘要。

        Returns:
            本调用首次完成当前槽位时为 True；迟到或重复终态为 False。
        """

        row = self.connection.execute(
            """
            SELECT round_id, participant_id FROM discussion_attempts
            WHERE id=? AND status IN ('dispatching', 'running')
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            return False
        changed = self.connection.execute(
            """
            UPDATE discussion_round_participants
            SET status=?, output_artifact=?, output_sha256=?, output_bytes=?,
                error_code=?, error_message=?, usage_json=?, activity_json=?,
                completed_at=?
            WHERE round_id=? AND participant_id=?
              AND current_attempt_id=? AND status='running'
            """,
            (
                status,
                output_artifact,
                output_sha256,
                output_bytes,
                error_code,
                error_message,
                usage_json,
                activity_json,
                completed_at,
                row["round_id"],
                row["participant_id"],
                attempt_id,
            ),
        ).rowcount
        if changed != 1:
            return False
        self.connection.execute(
            """
            UPDATE discussion_attempts
            SET status=?, output_artifact=?, output_sha256=?, output_bytes=?,
                error_code=?, error_message=?, activity_json=?,
                completed_at=? WHERE id=? AND status IN ('dispatching', 'running')
            """,
            (
                status,
                output_artifact,
                output_sha256,
                output_bytes,
                error_code,
                error_message,
                activity_json,
                completed_at,
                attempt_id,
            ),
        )
        return True

    def mark_running_attempts_host_lost(
        self,
        discussion_id: str,
        *,
        completed_at: str,
    ) -> tuple[str, ...]:
        """应用重启时封口旧 running attempt，并把槽位留给新 attempt。

        Args:
            discussion_id: 要恢复的研讨 ID。
            completed_at: 检测到 host loss 的时间。

        Returns:
            被标记为 host_lost 的 participant ID。
        """

        rows = self.connection.execute(
            """
            SELECT a.id, a.participant_id, a.round_id
            FROM discussion_attempts a
            JOIN discussion_rounds r ON r.id=a.round_id
            WHERE r.discussion_id=? AND r.status='running'
              AND a.status IN ('dispatching', 'running', 'needs_reconcile')
            """,
            (discussion_id,),
        ).fetchall()
        for row in rows:
            self.connection.execute(
                """
                UPDATE discussion_attempts
                SET status='host_lost', error_code='HOST_LOST',
                    error_message='应用退出时本次回答没有形成可发布终态', completed_at=?
                WHERE id=? AND status IN ('dispatching', 'running', 'needs_reconcile')
                """,
                (completed_at, row["id"]),
            )
            self.connection.execute(
                """
                UPDATE discussion_round_participants
                SET status='host_lost', error_code='HOST_LOST',
                    error_message='应用退出时本次回答没有形成可发布终态',
                    completed_at=?
                WHERE round_id=? AND participant_id=? AND current_attempt_id=?
                """,
                (completed_at, row["round_id"], row["participant_id"], row["id"]),
            )
        return tuple(str(row["participant_id"]) for row in rows)

    def mark_running_attempts_needs_reconcile(
        self,
        discussion_id: str,
        *,
        completed_at: str,
    ) -> tuple[str, ...]:
        """把协调器本地故障留下的 attempt 保留为待对账，而非伪装成 host loss。

        Args:
            discussion_id: 发生本地持久化或程序故障的研讨 ID。
            completed_at: 检测到协调器故障的时间。

        Returns:
            被标记为 needs_reconcile 的 participant ID。
        """

        rows = self.connection.execute(
            """
            SELECT a.id, a.participant_id, a.round_id
            FROM discussion_attempts a
            JOIN discussion_rounds r ON r.id=a.round_id
            WHERE r.discussion_id=? AND r.status='running'
              AND a.status IN ('dispatching', 'running')
            """,
            (discussion_id,),
        ).fetchall()
        for row in rows:
            self.connection.execute(
                """
                UPDATE discussion_attempts
                SET status='needs_reconcile', error_code='COORDINATOR_FAILURE',
                    error_message='协调器本地状态需要恢复对账', completed_at=?
                WHERE id=? AND status IN ('dispatching', 'running')
                """,
                (completed_at, row["id"]),
            )
            self.connection.execute(
                """
                UPDATE discussion_round_participants
                SET status='needs_reconcile', error_code=NULL, error_message=NULL,
                    completed_at=NULL
                WHERE round_id=? AND participant_id=? AND current_attempt_id=?
                """,
                (row["round_id"], row["participant_id"], row["id"]),
            )
        return tuple(str(row["participant_id"]) for row in rows)

    def round_is_terminal(self, round_id: str) -> bool:
        """判断一轮是否没有 pending/running/needs_reconcile 槽位。

        Args:
            round_id: 要查询的轮次 ID。

        Returns:
            所有槽位都已进入可发布终态时为 True。
        """

        row = self.connection.execute(
            """
            SELECT COUNT(*) AS pending FROM discussion_round_participants
            WHERE round_id=? AND status IN ('pending', 'running', 'needs_reconcile')
            """,
            (round_id,),
        ).fetchone()
        return int(row["pending"]) == 0

    def publish_round(
        self,
        discussion_id: str,
        round_id: str,
        *,
        publication_artifact: str,
        publication_sha256: str,
        publication_bytes: int,
        published_at: str,
        next_status: str,
    ) -> int | None:
        """以 SQLite 单事务完成整轮唯一公开和研讨状态推进。

        Args:
            discussion_id: 所属研讨 ID。
            round_id: 已有完整终态槽位的轮次 ID。
            publication_artifact: 事务前已经 durable 的发布清单相对路径。
            publication_sha256: 发布清单的 SHA-256。
            publication_bytes: 发布清单的 UTF-8 字节数。
            published_at: 共同公开时间。
            next_status: waiting_user、running 或 completed。

        Returns:
            首次发布后的新版本；该轮已发布时为 None。
        """

        if not self.round_is_terminal(round_id):
            return None
        changed = self.connection.execute(
            """
            UPDATE discussion_rounds
            SET status='published', publication_artifact=?, publication_sha256=?,
                publication_bytes=?, published_at=?
            WHERE id=? AND discussion_id=? AND status='running'
            """,
            (
                publication_artifact,
                publication_sha256,
                publication_bytes,
                published_at,
                round_id,
                discussion_id,
            ),
        ).rowcount
        if changed != 1:
            return None
        row = self.connection.execute(
            "SELECT version, number FROM discussions d JOIN discussion_rounds r ON r.discussion_id=d.id WHERE d.id=? AND r.id=?",
            (discussion_id, round_id),
        ).fetchone()
        version = int(row["version"]) + 1
        completed_at = published_at if next_status == "completed" else None
        self.connection.execute(
            """
            UPDATE discussions
            SET version=?, status=?, updated_at=?, completed_at=COALESCE(?, completed_at)
            WHERE id=?
            """,
            (version, next_status, published_at, completed_at, discussion_id),
        )
        self.append_event(
            discussion_id,
            "round_published",
            version,
            round_number=int(row["number"]),
            created_at=published_at,
        )
        return version

    def stop_discussion(
        self,
        discussion_id: str,
        *,
        expected_version: int,
        stopped_at: str,
        reason: str,
    ) -> int:
        """以 expected version 阻止后续调度并标记当前轮停止。

        Args:
            discussion_id: 要停止的研讨 ID。
            expected_version: 调用方看到的版本。
            stopped_at: 用户停止时间。
            reason: 不包含正文的停止说明。

        Returns:
            新研讨版本。
        """

        version = self.advance_version(
            discussion_id,
            expected_version=expected_version,
            allowed_statuses=("draft", "running", "waiting_user", "needs_reconcile"),
            updated_at=stopped_at,
            status="stopped",
        )
        self._mark_unpublished_slots_stop_requested(
            discussion_id,
        )
        self.connection.execute(
            """
            UPDATE discussions SET stopped_at=? WHERE id=?
            """,
            (stopped_at, discussion_id),
        )
        self.connection.execute(
            """
            UPDATE discussion_rounds SET status='stopped', stop_reason=?
            WHERE discussion_id=? AND status='running'
            """,
            (reason, discussion_id),
        )
        self.append_event(
            discussion_id,
            "discussion_stopped",
            version,
            created_at=stopped_at,
        )
        return version

    def _mark_unpublished_slots_stop_requested(
        self,
        discussion_id: str,
    ) -> None:
        """记录用户停止请求，等待 runtime 资源确认后再写 cancelled。

        Args:
            discussion_id: 要停止的研讨 ID。
        """

        self.connection.execute(
            """
            UPDATE discussion_attempts
            SET status='needs_reconcile', error_code='STOP_REQUESTED',
                error_message='用户已请求停止，运行工具资源正在对账', completed_at=NULL
            WHERE status IN ('dispatching', 'running', 'needs_reconcile')
              AND round_id IN (
                  SELECT id FROM discussion_rounds
                  WHERE discussion_id=? AND status='running'
              )
            """,
            (discussion_id,),
        )
        self.connection.execute(
            """
            UPDATE discussion_round_participants
            SET status='needs_reconcile', error_code=NULL,
                error_message=NULL, completed_at=NULL
            WHERE status IN ('pending', 'running', 'needs_reconcile')
              AND round_id IN (
                  SELECT id FROM discussion_rounds
                  WHERE discussion_id=? AND status='running'
              )
            """,
            (discussion_id,),
        )

    def mark_stopped_participant_cancelled(
        self,
        discussion_id: str,
        participant_id: str,
        *,
        completed_at: str,
    ) -> bool:
        """在 participant 资源确认关闭后登记控制面取消，不冒充 runtime interrupted。

        Args:
            discussion_id: 已 stopped 的研讨 ID。
            participant_id: 已确认没有残余 binding/runtime 的参与者。
            completed_at: 资源关闭核验完成时间。
        """

        attempts_changed = self.connection.execute(
            """
            UPDATE discussion_attempts
            SET status='cancelled', error_code='DISCUSSION_STOPPED',
                error_message='用户停止研讨后运行工具资源已关闭', completed_at=?
            WHERE participant_id=? AND status='needs_reconcile'
              AND round_id IN (
                  SELECT id FROM discussion_rounds
                  WHERE discussion_id=? AND status='stopped'
              )
            """,
            (completed_at, participant_id, discussion_id),
        ).rowcount
        slots_changed = self.connection.execute(
            """
            UPDATE discussion_round_participants
            SET status='cancelled', error_code='DISCUSSION_STOPPED',
                error_message='用户停止研讨后运行工具资源已关闭', completed_at=?
            WHERE participant_id=? AND status IN ('pending', 'running', 'needs_reconcile')
              AND round_id IN (
                  SELECT id FROM discussion_rounds
                  WHERE discussion_id=? AND status='stopped'
              )
            """,
            (completed_at, participant_id, discussion_id),
        ).rowcount
        return attempts_changed > 0 or slots_changed > 0

    def record_resource_state_changed(
        self,
        discussion_id: str,
        *,
        updated_at: str,
    ) -> int:
        """为 participant 资源状态的公开变化递增版本并追加事件。

        Args:
            discussion_id: 资源状态已经在同一事务更新的研讨 ID。
            updated_at: 对账完成时间。

        Returns:
            递增后的 discussion 版本。
        """

        row = self.connection.execute(
            "SELECT version FROM discussions WHERE id=?",
            (discussion_id,),
        ).fetchone()
        if row is None:
            raise DiscussionNotFoundError()
        version = int(row["version"]) + 1
        self.connection.execute(
            "UPDATE discussions SET version=?, updated_at=? WHERE id=?",
            (version, updated_at, discussion_id),
        )
        self.append_event(
            discussion_id,
            "resources_reconciled",
            version,
            created_at=updated_at,
        )
        return version

    def mark_needs_reconcile(self, discussion_id: str, *, updated_at: str) -> int:
        """把重启后仍有未完成轮的研讨标成待恢复并递增版本。

        Args:
            discussion_id: 所属研讨 ID。
            updated_at: 检测时间。

        Returns:
            新研讨版本。
        """

        row = self.connection.execute(
            "SELECT version, status FROM discussions WHERE id=?",
            (discussion_id,),
        ).fetchone()
        if row is None:
            raise DiscussionNotFoundError()
        if row["status"] not in {"draft", "running"}:
            return int(row["version"])
        version = int(row["version"]) + 1
        self.connection.execute(
            """
            UPDATE discussions SET status='needs_reconcile', version=?, updated_at=?
            WHERE id=? AND status IN ('draft', 'running')
            """,
            (version, updated_at, discussion_id),
        )
        self.append_event(
            discussion_id,
            "discussion_needs_reconcile",
            version,
            created_at=updated_at,
        )
        return version

    def resume_existing_round(self, discussion_id: str, *, updated_at: str) -> int:
        """把 needs_reconcile 研讨恢复为 running，不新建逻辑轮。

        Args:
            discussion_id: 要恢复的研讨 ID。
            updated_at: 恢复调度时间。

        Returns:
            恢复后的 discussion 版本。
        """

        row = self.connection.execute(
            "SELECT version, status FROM discussions WHERE id=?",
            (discussion_id,),
        ).fetchone()
        if row is None:
            raise DiscussionNotFoundError()
        if row["status"] == "running":
            return int(row["version"])
        if row["status"] != "needs_reconcile":
            raise DiscussionVersionConflictError(int(row["version"]))
        version = int(row["version"]) + 1
        self.connection.execute(
            """
            UPDATE discussions SET status='running', version=?, updated_at=?
            WHERE id=? AND status='needs_reconcile'
            """,
            (version, updated_at, discussion_id),
        )
        self.append_event(
            discussion_id,
            "discussion_resumed",
            version,
            created_at=updated_at,
        )
        return version

    def soft_delete(
        self,
        discussion_id: str,
        *,
        expected_version: int,
        deleted_at: str,
    ) -> int:
        """软删除已收口研讨，保留 Episode provenance 和幂等 tombstone。

        Args:
            discussion_id: 要删除的研讨 ID。
            expected_version: 调用方看到的版本。
            deleted_at: 删除时间。

        Returns:
            tombstone 的最终版本。
        """

        version = self.advance_version(
            discussion_id,
            expected_version=expected_version,
            allowed_statuses=("completed", "stopped"),
            updated_at=deleted_at,
            status="deleted",
        )
        self.connection.execute(
            "UPDATE discussions SET deleted_at=? WHERE id=?",
            (deleted_at, discussion_id),
        )
        return version

    def set_episode_id(
        self, discussion_id: str, episode_id: str, *, updated_at: str
    ) -> bool:
        """首次登记聚合 Episode ID，重试同一 ID 保持幂等。

        Args:
            discussion_id: 来源研讨 ID。
            episode_id: 确定性聚合 Episode ID。
            updated_at: 登记时间。

        Returns:
            首次写入或已有相同 ID 时为 True，已有不同 ID 时为 False。
        """

        row = self.connection.execute(
            "SELECT episode_id FROM discussions WHERE id=?",
            (discussion_id,),
        ).fetchone()
        if row is None:
            raise DiscussionNotFoundError()
        if row["episode_id"] is not None:
            return str(row["episode_id"]) == episode_id
        self.connection.execute(
            "UPDATE discussions SET episode_id=?, updated_at=? WHERE id=?",
            (episode_id, updated_at, discussion_id),
        )
        return True

    def list_recoverable_discussion_ids(self) -> tuple[str, ...]:
        """返回应用启动时需要 reconcile 或继续运行的研讨 ID。"""

        rows = self.connection.execute(
            """
            SELECT id FROM discussions
            WHERE status IN ('running', 'needs_reconcile')
            ORDER BY created_at, id
            """
        ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def list_discussion_ids_for_transcript_rebuild(self) -> tuple[str, ...]:
        """返回启动时应重建派生 transcript 的全部未删除研讨。

        Returns:
            按创建顺序排列的 discussion ID。
        """

        rows = self.connection.execute(
            """
            SELECT id FROM discussions
            WHERE status != 'deleted'
            ORDER BY created_at, id
            """
        ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def list_pending_episode_discussion_ids(self) -> tuple[str, ...]:
        """返回已经收口但尚未登记聚合 Episode 的研讨 ID。"""

        rows = self.connection.execute(
            """
            SELECT id FROM discussions
            WHERE status IN ('completed', 'stopped') AND episode_id IS NULL
            ORDER BY updated_at, id
            """
        ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def record_integrity_issue(
        self,
        discussion_id: str,
        *,
        detected_at: str,
    ) -> None:
        """幂等登记 artifact 双副本或完整性故障的对账 outbox。

        Args:
            discussion_id: 检测到漂移的研讨 ID。
            detected_at: 首次或最近检测时间。
        """

        self.connection.execute(
            """
            INSERT INTO discussion_integrity_issues(
                discussion_id, issue_code, detected_at
            ) VALUES (?, 'ARTIFACT_INTEGRITY', ?)
            ON CONFLICT(discussion_id) DO UPDATE SET detected_at=excluded.detected_at
            """,
            (discussion_id, detected_at),
        )

    def clear_integrity_issue(self, discussion_id: str) -> None:
        """在全部 artifact 重新验真后清除 durable 对账 outbox。

        Args:
            discussion_id: 已经重新验真的研讨 ID。
        """

        self.connection.execute(
            "DELETE FROM discussion_integrity_issues WHERE discussion_id=?",
            (discussion_id,),
        )

    def list_integrity_issue_discussion_ids(self) -> tuple[str, ...]:
        """返回等待 artifact 完整性复核的研讨 ID。"""

        rows = self.connection.execute(
            """
            SELECT discussion_id FROM discussion_integrity_issues
            ORDER BY detected_at, discussion_id
            """
        ).fetchall()
        return tuple(str(row["discussion_id"]) for row in rows)

    def list_terminal_resource_reconcile_discussion_ids(self) -> tuple[str, ...]:
        """返回已收口但仍有 participant binding 待核验关闭的研讨。

        Returns:
            completed/stopped discussion ID，按更新时间稳定排序。
        """

        rows = self.connection.execute(
            """
            SELECT DISTINCT d.id, d.updated_at
            FROM discussions d
            JOIN discussion_participants p ON p.discussion_id=d.id
            WHERE d.status IN ('completed', 'stopped') AND p.status != 'closed'
            ORDER BY d.updated_at, d.id
            """
        ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def list_pending_profile_messages(self) -> tuple[UserMessage, ...]:
        """返回只含顶层用户原话的 discussion Profile backlog。"""

        rows = self.connection.execute(
            """
            SELECT * FROM discussion_user_messages
            WHERE author_role='user' AND profile_status='pending'
              AND discussion_id IN (
                  SELECT id FROM discussions WHERE status != 'deleted'
              )
            ORDER BY created_at, discussion_id, sequence
            """
        ).fetchall()
        return tuple(self._message_from_row(row) for row in rows)

    def mark_profile_message_processed(
        self, profile_source_id: str, *, status: str = "processed"
    ) -> bool:
        """按独立来源 ID 幂等推进顶层用户消息的 Profile 水位。

        Args:
            profile_source_id: ``discussion:<id>:message:<id>`` 稳定来源 ID。
            status: processed 或 ignored。

        Returns:
            找到并更新或已经处于目标状态时为 True。
        """

        row = self.connection.execute(
            """
            SELECT profile_status FROM discussion_user_messages
            WHERE profile_source_id=?
            """,
            (profile_source_id,),
        ).fetchone()
        if row is None:
            return False
        if row["profile_status"] == status:
            return True
        self.connection.execute(
            """
            UPDATE discussion_user_messages SET profile_status=?
            WHERE profile_source_id=? AND profile_status='pending'
            """,
            (status, profile_source_id),
        )
        return True

    def get_command_result(
        self,
        discussion_id: str,
        command_id: str,
        *,
        command_type: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        """在状态检查前查找幂等命令结果，并拒绝身份挪用。

        Args:
            discussion_id: 命令所属研讨 ID。
            command_id: 客户端稳定命令 ID。
            command_type: 本次命令类型。
            request_hash: 不保存正文的规范化请求指纹。

        Returns:
            已提交命令的 JSON 结果；首次请求为 None。

        Raises:
            DiscussionCommandConflictError: 同一 ID 对应不同请求。
        """

        row = self.connection.execute(
            """
            SELECT command_type, request_hash, result_json
            FROM discussion_commands WHERE discussion_id=? AND command_id=?
            """,
            (discussion_id, command_id),
        ).fetchone()
        if row is None:
            return None
        if row["command_type"] != command_type or row["request_hash"] != request_hash:
            raise DiscussionCommandConflictError()
        result = json.loads(str(row["result_json"]))
        return result if isinstance(result, dict) else {}

    def record_command_result(
        self,
        discussion_id: str,
        command_id: str,
        *,
        command_type: str,
        request_hash: str,
        result: dict[str, Any],
        created_at: str,
    ) -> None:
        """保存写命令收据，使响应丢失后的重试不再执行状态迁移。

        Args:
            discussion_id: 命令所属研讨 ID。
            command_id: 客户端稳定命令 ID。
            command_type: 命令类型。
            request_hash: 规范化请求指纹。
            result: 可安全公开的命令结果摘要。
            created_at: 首次提交时间。
        """

        self.connection.execute(
            """
            INSERT INTO discussion_commands(
                discussion_id, command_id, command_type, request_hash,
                result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                discussion_id,
                command_id,
                command_type,
                request_hash,
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                created_at,
            ),
        )

    def update_command_result(
        self,
        discussion_id: str,
        command_id: str,
        *,
        command_type: str,
        request_hash: str,
        result: dict[str, Any],
    ) -> None:
        """更新已经预留的外部副作用命令收据。

        Args:
            discussion_id: 命令所属研讨 ID。
            command_id: 客户端稳定命令 ID。
            command_type: 必须与预留记录一致的命令类型。
            request_hash: 必须与预留记录一致的请求指纹。
            result: 外部操作完成后的安全结果摘要。

        Raises:
            DiscussionCommandConflictError: 预留记录不存在或身份不一致。
        """

        changed = self.connection.execute(
            """
            UPDATE discussion_commands SET result_json=?
            WHERE discussion_id=? AND command_id=?
              AND command_type=? AND request_hash=?
            """,
            (
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                discussion_id,
                command_id,
                command_type,
                request_hash,
            ),
        ).rowcount
        if changed != 1:
            raise DiscussionCommandConflictError()

    def set_user_mark(
        self,
        discussion_id: str,
        source_ref: str,
        *,
        marked: bool,
        created_at: str,
    ) -> None:
        """幂等设置一个公开结果是否由用户标记。

        Args:
            discussion_id: 标记所属研讨 ID。
            source_ref: 轮次和参与者组成的稳定结果引用。
            marked: True 写入 promotion，False 删除 promotion。
            created_at: 首次标记时间。
        """

        if marked:
            self.connection.execute(
                """
                INSERT INTO discussion_promotions(
                    id, discussion_id, kind, source_ref, created_at
                ) VALUES (?, ?, 'user_marked', ?, ?)
                ON CONFLICT(discussion_id, kind, source_ref) DO NOTHING
                """,
                (
                    hashlib.sha256(
                        f"{discussion_id}:user_marked:{source_ref}".encode()
                    ).hexdigest()[:32],
                    discussion_id,
                    source_ref,
                    created_at,
                ),
            )
            return
        self.connection.execute(
            """
            DELETE FROM discussion_promotions
            WHERE discussion_id=? AND kind='user_marked' AND source_ref=?
            """,
            (discussion_id, source_ref),
        )

    def list_user_mark_sources(self, discussion_id: str) -> frozenset[str]:
        """返回一场研讨当前仍有效的用户标记来源。"""

        rows = self.connection.execute(
            """
            SELECT source_ref FROM discussion_promotions
            WHERE discussion_id=? AND kind='user_marked'
            ORDER BY source_ref
            """,
            (discussion_id,),
        ).fetchall()
        return frozenset(str(row["source_ref"]) for row in rows)

    def list_handoff_results(self, discussion_id: str) -> tuple[dict[str, Any], ...]:
        """返回已经创建成功的普通 Agent 交接收据。"""

        rows = self.connection.execute(
            """
            SELECT result_json, created_at FROM discussion_commands
            WHERE discussion_id=? AND command_type='handoff'
            ORDER BY created_at, command_id
            """,
            (discussion_id,),
        ).fetchall()
        results = []
        for row in rows:
            payload = json.loads(str(row["result_json"]))
            if isinstance(payload, dict) and payload.get("status") == "started":
                results.append({**payload, "created_at": str(row["created_at"])})
        return tuple(results)

    def list_events(
        self,
        discussion_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[dict[str, Any], ...]:
        """读取不含封闭正文的持久 discussion 事件。

        Args:
            discussion_id: 所属研讨 ID。
            after_sequence: 只返回更大 sequence 的事件。
            limit: 单次最多返回数量。

        Returns:
            可直接投影到 discussion SSE 的事件摘要。
        """

        rows = self.connection.execute(
            """
            SELECT sequence, event_type, version, round_number, created_at
            FROM discussion_events
            WHERE discussion_id=? AND sequence>?
            ORDER BY sequence LIMIT ?
            """,
            (discussion_id, after_sequence, limit),
        ).fetchall()
        return tuple(
            {
                "sequence": int(row["sequence"]),
                "type": str(row["event_type"]),
                "version": int(row["version"]),
                "round_number": (
                    int(row["round_number"])
                    if row["round_number"] is not None
                    else None
                ),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        )

    def append_event(
        self,
        discussion_id: str,
        event_type: str,
        version: int,
        *,
        created_at: str,
        round_number: int | None = None,
    ) -> None:
        """追加一条不含 participant 正文的持久状态事件。

        Args:
            discussion_id: 所属研讨 ID。
            event_type: 状态事件类型。
            version: 事件对应的 discussion 版本。
            created_at: 事件时间。
            round_number: 相关轮号；无轮次时为 None。
        """

        self.connection.execute(
            """
            INSERT INTO discussion_events(
                discussion_id, event_type, version, round_number, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (discussion_id, event_type, version, round_number, created_at),
        )

    def advance_version(
        self,
        discussion_id: str,
        *,
        expected_version: int,
        allowed_statuses: Sequence[str],
        updated_at: str,
        status: str | None = None,
        active_round_number: int | None = None,
    ) -> int:
        """按 expected version 和允许状态执行一次 CAS 状态更新。

        Args:
            discussion_id: 要更新的研讨 ID。
            expected_version: 调用方看到的版本。
            allowed_statuses: 本命令允许的前置状态。
            updated_at: 状态变化时间。
            status: 可选的新状态；None 表示保持。
            active_round_number: 可选的新当前轮号。

        Returns:
            更新后的版本。

        Raises:
            DiscussionNotFoundError: 记录不存在或已删除。
            DiscussionVersionConflictError: 版本或前置状态不匹配。
        """

        row = self.connection.execute(
            "SELECT version, status FROM discussions WHERE id=?",
            (discussion_id,),
        ).fetchone()
        if row is None or row["status"] == "deleted":
            raise DiscussionNotFoundError()
        current_version = int(row["version"])
        if current_version != expected_version or row["status"] not in allowed_statuses:
            raise DiscussionVersionConflictError(current_version)
        new_version = current_version + 1
        changed = self.connection.execute(
            """
            UPDATE discussions
            SET version=?, status=COALESCE(?, status),
                active_round_number=COALESCE(?, active_round_number), updated_at=?
            WHERE id=? AND version=?
            """,
            (
                new_version,
                status,
                active_round_number,
                updated_at,
                discussion_id,
                current_version,
            ),
        ).rowcount
        if changed != 1:
            latest = self.connection.execute(
                "SELECT version FROM discussions WHERE id=?",
                (discussion_id,),
            ).fetchone()
            raise DiscussionVersionConflictError(
                int(latest["version"]) if latest is not None else current_version
            )
        return new_version

    def _insert_user_message(self, message: UserMessage) -> None:
        """写入一条已经完成来源身份校验的顶层用户消息。

        Args:
            message: 要保存的用户原话。
        """

        self.connection.execute(
            """
            INSERT INTO discussion_user_messages(
                id, discussion_id, sequence, after_round_number, author_role,
                target_scope, target_participant_id, body, message_artifact,
                message_sha256, message_bytes, profile_status, profile_source_id,
                created_at
            ) VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.discussion_id,
                message.sequence,
                message.after_round_number,
                message.target_scope,
                message.target_participant_id,
                message.body,
                message.message_artifact,
                message.message_sha256,
                message.message_bytes,
                message.profile_status,
                message.profile_source_id,
                message.created_at,
            ),
        )

    def _round_from_row(self, row: sqlite3.Row) -> DiscussionRound:
        """把 round 行和稳定槽位转换为不可变对象。

        Args:
            row: discussion_rounds 查询行。

        Returns:
            带 position 排序结果槽位的轮次。
        """

        results = tuple(
            self._result_from_row(item)
            for item in self.connection.execute(
                """
                SELECT * FROM discussion_round_participants
                WHERE round_id=? ORDER BY position
                """,
                (row["id"],),
            ).fetchall()
        )
        return DiscussionRound(
            id=str(row["id"]),
            discussion_id=str(row["discussion_id"]),
            number=int(row["number"]),
            kind=row["kind"],
            status=row["status"],
            public_context_hash=str(row["public_context_hash"]),
            input_artifact=str(row["input_artifact"]),
            input_bytes=int(row["input_bytes"]),
            publication_artifact=(
                str(row["publication_artifact"])
                if row["publication_artifact"]
                else None
            ),
            publication_sha256=(
                str(row["publication_sha256"]) if row["publication_sha256"] else None
            ),
            publication_bytes=(
                int(row["publication_bytes"])
                if row["publication_bytes"] is not None
                else None
            ),
            started_at=str(row["started_at"]),
            published_at=str(row["published_at"]) if row["published_at"] else None,
            stop_reason=str(row["stop_reason"]) if row["stop_reason"] else None,
            results=results,
        )

    @staticmethod
    def _participant_from_row(row: sqlite3.Row) -> DiscussionParticipant:
        """把 participant 查询行转换为不可变对象。

        Args:
            row: discussion_participants 查询行。

        Returns:
            对应 participant 对象。
        """

        return DiscussionParticipant(
            id=str(row["id"]),
            discussion_id=str(row["discussion_id"]),
            position=int(row["position"]),
            name=str(row["name"]),
            runtime=Runtime(str(row["runtime"])),
            connection_id=str(row["connection_id"]),
            connection_name=(
                str(row["connection_name"]) if row["connection_name"] else None
            ),
            model=str(row["model"]),
            effective_model=(
                str(row["effective_model"])
                if row["effective_model"]
                else str(row["model"])
            ),
            effort=str(row["effort"]) if row["effort"] else None,
            session_configuration_id=(
                str(row["session_configuration_id"])
                if row["session_configuration_id"]
                else None
            ),
            permission_mode=(
                str(row["permission_mode"])
                if row["permission_mode"]
                else "dontAsk"
                if str(row["runtime"]) == "claude_code"
                else None
            ),
            permission_preset=(
                str(row["permission_preset"])
                if row["permission_preset"]
                else "read-only"
                if str(row["runtime"]) == "codex"
                else None
            ),
            memory_enabled=bool(row["memory_enabled"]),
            profile_enabled=bool(row["profile_enabled"]),
            self_enabled=bool(row["self_enabled"]),
            connection_identity_version=(
                int(row["connection_identity_version"])
                if row["connection_identity_version"] is not None
                else None
            ),
            owner_ref=str(row["owner_ref"]),
            agent_session_id=(
                str(row["agent_session_id"]) if row["agent_session_id"] else None
            ),
            native_session_id=(
                str(row["native_session_id"]) if row["native_session_id"] else None
            ),
            status=row["status"],
            capability_version=(
                str(row["capability_version"]) if row["capability_version"] else None
            ),
            capability_source=(
                str(row["capability_source"]) if row["capability_source"] else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> UserMessage:
        """把顶层用户消息行转换为不可变对象。

        Args:
            row: discussion_user_messages 查询行。

        Returns:
            对应用户消息对象。
        """

        return UserMessage(
            id=str(row["id"]),
            discussion_id=str(row["discussion_id"]),
            sequence=int(row["sequence"]),
            after_round_number=int(row["after_round_number"]),
            target_scope=row["target_scope"],
            target_participant_id=(
                str(row["target_participant_id"])
                if row["target_participant_id"]
                else None
            ),
            body=str(row["body"]),
            message_artifact=str(row["message_artifact"]),
            message_sha256=str(row["message_sha256"]),
            message_bytes=int(row["message_bytes"]),
            profile_status=row["profile_status"],
            profile_source_id=str(row["profile_source_id"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _result_from_row(row: sqlite3.Row) -> ParticipantResult:
        """把 round participant 槽位行转换为不可变对象。

        Args:
            row: discussion_round_participants 查询行。

        Returns:
            对应结果槽位。
        """

        return ParticipantResult(
            participant_id=str(row["participant_id"]),
            position=int(row["position"]),
            status=row["status"],
            current_attempt_id=(
                str(row["current_attempt_id"]) if row["current_attempt_id"] else None
            ),
            output_artifact=(
                str(row["output_artifact"]) if row["output_artifact"] else None
            ),
            output_sha256=(str(row["output_sha256"]) if row["output_sha256"] else None),
            output_bytes=(
                int(row["output_bytes"]) if row["output_bytes"] is not None else None
            ),
            error_code=str(row["error_code"]) if row["error_code"] else None,
            error_message=(str(row["error_message"]) if row["error_message"] else None),
            usage_json=str(row["usage_json"]) if row["usage_json"] else None,
            activity_json=(
                str(row["activity_json"]) if row["activity_json"] else None
            ),
            started_at=str(row["started_at"]) if row["started_at"] else None,
            completed_at=(str(row["completed_at"]) if row["completed_at"] else None),
        )


@contextmanager
def open_discussion_repository(
    db_path: str | Path | None = None,
) -> Iterator[DiscussionRepository]:
    """打开主库、应用迁移并提供一个短生命周期 repository。

    Args:
        db_path: 测试可注入的显式数据库路径；正式运行省略。

    Yields:
        使用独立 SQLite 连接的 repository。
    """

    connection = create_db(db_path)
    try:
        run_migrations(connection)
        yield DiscussionRepository(connection)
    finally:
        connection.close()
