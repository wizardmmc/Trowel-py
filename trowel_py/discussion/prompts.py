"""从已公开事实确定性构造每轮共享的普通用户提示词。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.models import Discussion, DiscussionRound


@dataclass(frozen=True)
class RoundPrompt:
    """保存一轮共享 prompt 及其精确字节指纹。

    Attributes:
        text: 将逐字发送给所有 participant 的普通用户输入。
        sha256: ``text.encode('utf-8')`` 的 SHA-256。
    """

    text: str
    sha256: str


def build_round_prompt(
    discussion: Discussion,
    *,
    round_number: int,
    kind: str,
    artifacts: DiscussionArtifactStore,
) -> RoundPrompt:
    """只读取上一轮已发布结果和其后用户补充，构造共同输入。

    Args:
        discussion: 当前 SQLite 聚合事实。
        round_number: 即将开始的逻辑轮号。
        kind: regular 或 final。
        artifacts: 用于读取已发布 final 的完整性校验入口。

    Returns:
        所有 participant 共用的 prompt 和 hash。

    Raises:
        ValueError: 上一轮尚未共同公开、槽位引用损坏或 kind 无效。
    """

    if kind not in {"regular", "final"}:
        raise ValueError("unknown discussion round kind")
    if round_number < 1:
        raise ValueError("discussion round number must be positive")
    if not discussion.messages:
        raise ValueError("discussion initial user message is missing")
    topic = artifacts.read_user_message_body(discussion.messages[0])
    if topic != discussion.topic:
        raise ValueError("discussion topic and initial message differ")
    lines = [
        "这是一场由多位讨论者平等参与的研讨。你只需要像收到普通用户提示词一样回答。",
        "同一轮的其他讨论者也会收到完全相同的公开内容；你看不到他们本轮尚未公开的回答。",
        "请给出完整、可以单独阅读的回答。即使本次是应用重启后的重试，也不要只续写半句话。",
        "不要声称代表其他讨论者，也不要假设存在隐藏主持人或最终裁判。",
        "",
        "【初始议题】",
        topic,
    ]
    previous = _previous_published_round(discussion, round_number)
    if previous is None:
        if round_number != 1:
            raise ValueError("discussion previous round is missing")
        lines.extend(
            [
                "",
                "【本轮任务】",
                "独立分析这个议题，说明当前看法、理由、关键不确定性和建议的下一步。",
            ]
        )
    else:
        artifacts.verify_publication(discussion, previous)
        participant_by_id = {item.id: item for item in discussion.participants}
        lines.extend(["", f"【上一轮（第 {previous.number} 轮）公开发言】"])
        for result in previous.results:
            participant = participant_by_id[result.participant_id]
            lines.extend(["", f"{participant.name}："])
            if result.status == "succeeded" and result.output_artifact:
                lines.append(
                    artifacts.read_text(
                        result.output_artifact,
                        expected_sha256=result.output_sha256,
                        expected_bytes=result.output_bytes,
                    )
                )
            else:
                reason = result.error_message or result.status
                lines.append(f"[{result.status}] {reason}")
        supplements = [
            message
            for message in discussion.messages
            if message.after_round_number == previous.number
        ]
        if supplements:
            lines.extend(["", "【用户在上一轮后的补充】"])
            for message in supplements:
                if message.target_scope == "all":
                    target = "给全体"
                else:
                    target = (
                        "只要求 "
                        + participant_by_id[message.target_participant_id or ""].name
                        + " 回应"
                    )
                lines.append(f"- {target}：{artifacts.read_user_message_body(message)}")
        lines.extend(["", "【本轮任务】"])
        if kind == "final":
            lines.append(
                "请做收尾发言：给出自己的当前立场、相较上一轮发生的变化、仍未解决的问题，"
                "以及建议用户接下来如何处理。不要替其他讨论者合并成唯一答案。"
            )
        else:
            lines.append(
                "根据上一轮全部公开发言和用户补充继续讨论：指出赞同、分歧和遗漏，"
                "必要时修正自己的看法，并给出本轮完整观点。"
            )
    text = "\n".join(lines).strip() + "\n"
    return RoundPrompt(text=text, sha256=hashlib.sha256(text.encode()).hexdigest())


def _previous_published_round(
    discussion: Discussion, round_number: int
) -> DiscussionRound | None:
    """返回紧邻且已经共同公开的上一轮。

    Args:
        discussion: 当前研讨聚合。
        round_number: 即将开始的轮号。

    Returns:
        round_number-1 的 published 轮；首轮为 None。

    Raises:
        ValueError: 紧邻上一轮存在但未共同公开。
    """

    if round_number == 1:
        return None
    previous = next(
        (item for item in discussion.rounds if item.number == round_number - 1),
        None,
    )
    if previous is None or previous.status != "published":
        raise ValueError("discussion previous round is not published")
    return previous
