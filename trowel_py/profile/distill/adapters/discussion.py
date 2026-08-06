"""把 discussion 顶层用户消息适配为独立 Profile 提炼来源。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.models import Discussion, UserMessage
from trowel_py.discussion.repository import DiscussionRepository
from trowel_py.profile.distill.models import ProfileDistillSession
from trowel_py.profile.distill.gate import DistillError
from trowel_py.profile.distill.sources.models import (
    ProfileDistillSource,
    ProfileJournalSlice,
)

DiscussionRepositoryOpener = Callable[[], AbstractContextManager[DiscussionRepository]]


@dataclass(frozen=True)
class DiscussionDistillCandidate:
    """保存一条用户原话及同一研讨中更早的用户上下文。

    Attributes:
        discussion: 消息所属研讨，用于工作目录和序列身份。
        message: 本次唯一允许形成 Profile 证据的用户原话。
        history: 同一研讨中更早的顶层用户原话，只用于理解指代。
        artifacts: 校验并定位消息 JSON 的 artifact 存储。
        repository_opener: 成功后推进 SQLite 独立水位的短连接工厂。
    """

    discussion: Discussion
    message: UserMessage
    history: tuple[UserMessage, ...]
    artifacts: DiscussionArtifactStore
    repository_opener: DiscussionRepositoryOpener

    @property
    def runtime(self) -> str:
        """返回宿主级 discussion 来源类型。"""

        return "discussion"

    @property
    def label(self) -> str:
        """返回日志和溯源共用的稳定来源身份。"""

        return self.source_id

    @property
    def source_id(self) -> str:
        """返回创建消息时冻结的独立 Profile 来源 ID。"""

        return self.message.profile_source_id

    @property
    def completed_at(self) -> str:
        """把用户消息写入时间作为来源完成时间。"""

        return self.message.created_at

    @property
    def registered_at(self) -> str:
        """返回研讨创建时间用于跨来源稳定排序。"""

        return self.discussion.created_at

    @property
    def sequence_id(self) -> str:
        """返回同一研讨的阻断序列，防止失败后越过后续消息。"""

        return f"discussion:{self.discussion.id}"

    @property
    def session(self) -> ProfileDistillSession:
        """用研讨身份和原工作目录满足统一候选接口。"""

        return ProfileDistillSession(
            native_session_id=self.discussion.id,
            workdir=self.discussion.workdir,
        )

    def build_source(self) -> ProfileDistillSource:
        """把更早用户消息作为 context，把当前消息作为唯一 target。"""

        try:
            context = tuple(
                ProfileJournalSlice(str(self._verified_message_path(item)))
                for item in self.history
            )
            target_path = self._verified_message_path(self.message)
        except (OSError, UnicodeError, ValueError) as exc:
            with self.repository_opener() as repository, repository.transaction():
                repository.record_integrity_issue(
                    self.discussion.id,
                    detected_at=self.completed_at,
                )
            raise DistillError("discussion evidence integrity check failed") from exc
        return ProfileDistillSource(
            runtime="discussion",
            source_id=self.source_id,
            context=context,
            target=(ProfileJournalSlice(str(target_path)),),
            completed_at=self.completed_at,
        )

    def _verified_message_path(self, message: UserMessage) -> Path:
        """同时核对 artifact 身份、正文和 SQLite 字节数副本。

        Args:
            message: 要作为 Profile 证据的顶层用户消息。

        Returns:
            已完整校验的消息 artifact 绝对路径。
        """

        self.artifacts.read_user_message_body(message)
        return self.artifacts.verified_path(
            message.message_artifact,
            expected_sha256=message.message_sha256,
            expected_bytes=message.message_bytes,
        )

    def mark_processed(self, root: Path, *, at: str) -> None:
        """在建议队列落盘后推进消息自己的 SQLite Profile 水位。

        Args:
            root: 统一候选接口传入的 Memory 根；discussion 水位不使用该路径。
            at: 处理完成时间；当前表只保存状态，保留参数用于接口一致性。
        """

        del root, at
        with self.repository_opener() as repository, repository.transaction():
            if not repository.mark_profile_message_processed(self.source_id):
                raise ValueError("discussion profile source no longer exists")


def build_discussion_backlog(
    discussions: Sequence[Discussion],
    artifacts: DiscussionArtifactStore,
    repository_opener: DiscussionRepositoryOpener,
) -> list[DiscussionDistillCandidate]:
    """按研讨内消息顺序构造尚未处理的顶层用户来源。

    Args:
        discussions: 包含完整用户消息的未删除研讨聚合。
        artifacts: discussion artifact 存储。
        repository_opener: 处理成功后推进水位使用的仓储工厂。

    Returns:
        只含 ``profile_status=pending`` 用户消息的候选列表。
    """

    backlog: list[DiscussionDistillCandidate] = []
    for discussion in discussions:
        history: list[UserMessage] = []
        for message in discussion.messages:
            if message.profile_status == "pending":
                backlog.append(
                    DiscussionDistillCandidate(
                        discussion=discussion,
                        message=message,
                        history=tuple(history),
                        artifacts=artifacts,
                        repository_opener=repository_opener,
                    )
                )
            history.append(message)
    return backlog
