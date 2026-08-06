"""把一场已经收口的研讨确定性写成唯一聚合 Episode。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.models import Discussion
from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.store.repository import MemoryStore


class AggregateEpisodeStore(Protocol):
    """约束 discussion 需要的宿主级 Episode 写入能力。"""

    def write_aggregate_episode(
        self,
        *,
        episode_id: str,
        source_kind: str,
        source_ref: str,
        registered_at: str,
        activity_date: str,
        body: str,
    ) -> str:
        """写入或幂等覆盖一条聚合 Episode。"""
        ...


class DiscussionEpisodeWriter:
    """从 SQLite 已公开事实和校验过的正文 artifact 生成聚合 Episode。"""

    def __init__(
        self,
        artifacts: DiscussionArtifactStore,
        store: AggregateEpisodeStore | None = None,
        *,
        memory_root: Path | None = None,
    ) -> None:
        """保存 artifact 读取器和 Memory 写入门面。

        Args:
            artifacts: 用于读取已发布 participant 正文的私有 artifact 存储。
            store: 测试可注入的聚合 Episode 存储；省略时创建 ``MemoryStore``。
            memory_root: ``store`` 省略时使用的 Memory 根；正式运行自动解析。
        """

        self._artifacts = artifacts
        self._store = store or MemoryStore(memory_root or resolve_memory_root())

    def write(self, discussion: Discussion) -> str:
        """为 completed 或 stopped 研讨写入确定性唯一 Episode。

        只读取 ``published`` 轮。被停止轮的封闭输出不会借 Episode 绕过共同
        发布边界；该轮只记录各槽位最终状态。正文没有隐藏总结，分歧和未决项
        保留为逐方原话及明确的未完成状态。

        Args:
            discussion: 已经收尾或停止的最新研讨聚合。

        Returns:
            ``discussion-<discussion_id>`` 稳定 Episode ID。

        Raises:
            ValueError: 研讨尚未收口。
        """

        if discussion.status not in {"completed", "stopped"}:
            raise ValueError("discussion episode requires a finalized discussion")
        episode_id = f"discussion-{discussion.id}"
        activity_time = (
            discussion.completed_at or discussion.stopped_at or discussion.updated_at
        )
        activity_date = activity_time[:10]
        source_ref = f"discussions/{discussion.id}/transcript.md"
        return self._store.write_aggregate_episode(
            episode_id=episode_id,
            source_kind="discussion",
            source_ref=source_ref,
            registered_at=discussion.created_at,
            activity_date=activity_date,
            body=self._render_body(discussion, source_ref=source_ref),
        )

    def _render_body(self, discussion: Discussion, *, source_ref: str) -> str:
        """把研讨事实渲染成不做语义归纳的 Markdown 正文。

        Args:
            discussion: 已收口研讨聚合。
            source_ref: 完整累加记录相对于应用数据根的引用。

        Returns:
            适合放在 Episode 日期标题下的正文。
        """

        participant_by_id = {item.id: item for item in discussion.participants}
        lines = [
            "### 研讨",
            "",
            f"- 议题：{discussion.topic}",
            f"- 状态：{discussion.status}",
            f"- 完整记录：{source_ref}",
            "- 参与者："
            + "、".join(
                f"{item.name}（{item.runtime.value}/{item.model}）"
                for item in discussion.participants
            ),
        ]
        for round_record in discussion.rounds:
            lines.extend(
                [
                    "",
                    f"#### 第 {round_record.number} 轮（{round_record.kind}）",
                    "",
                ]
            )
            if round_record.status != "published":
                lines.append(
                    f"该轮未共同公开，状态为 {round_record.status}；封闭回答未写入 Episode。"
                )
                for result in round_record.results:
                    participant = participant_by_id[result.participant_id]
                    lines.append(f"- {participant.name}：{result.status}")
                continue
            for result in round_record.results:
                participant = participant_by_id[result.participant_id]
                lines.extend([f"##### {participant.name}", ""])
                if result.status == "succeeded" and result.output_artifact:
                    lines.append(
                        self._artifacts.read_text(
                            result.output_artifact,
                            expected_sha256=result.output_sha256,
                            expected_bytes=result.output_bytes,
                        ).rstrip()
                    )
                else:
                    lines.append(
                        f"[{result.status}] {result.error_message or result.error_code or '无回答'}"
                    )
                lines.append("")
        lines.extend(
            [
                "#### 分歧与未决项",
                "",
                "未调用主控模型另行总结；逐方原话、失败槽位和未公开轮即为可追溯现场。",
            ]
        )
        return "\n".join(lines).rstrip()
