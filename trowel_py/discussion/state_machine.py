"""集中定义研讨轮次是否可开始、可发布和发布后的确定性状态。"""

from __future__ import annotations

from trowel_py.discussion.models import Discussion, DiscussionRound

_TERMINAL_RESULT_STATUSES = frozenset(
    {
        "succeeded",
        "failed",
        "limited",
        "timed_out",
        "interrupted",
        "host_lost",
    }
)


def round_ready_to_publish(round_record: DiscussionRound) -> bool:
    """判断一轮是否拥有完整槽位且所有槽位都进入明确终态。

    Args:
        round_record: 要检查的轮次。

    Returns:
        轮次仍在 running、至少一位参与者且全部槽位终态时为 True。
    """

    return (
        round_record.status == "running"
        and bool(round_record.results)
        and all(
            item.status in _TERMINAL_RESULT_STATUSES for item in round_record.results
        )
    )


def status_after_publication(
    discussion: Discussion,
    round_record: DiscussionRound,
) -> str:
    """返回共同发布后应写入 discussion 的生命周期状态。

    Args:
        discussion: 当前研讨配置与状态。
        round_record: 即将发布的完整终态轮。

    Returns:
        final 轮为 completed；自动模式尚未到上限时为 running；其他情况等待用户。
    """

    if round_record.kind == "final":
        return "completed"
    if (
        discussion.progression_mode == "automatic"
        and discussion.max_rounds is not None
        and round_record.number < discussion.max_rounds
    ):
        return "running"
    return "waiting_user"


def should_automatically_continue(
    discussion: Discussion,
    published_round: DiscussionRound,
) -> bool:
    """判断普通轮公开后是否应自动创建下一轮。

    Args:
        discussion: 已应用 publication 状态迁移的研讨。
        published_round: 刚共同公开的普通轮。

    Returns:
        仅自动模式、非 final 且尚未达到用户上限时为 True。
    """

    return (
        discussion.progression_mode == "automatic"
        and published_round.kind == "regular"
        and discussion.max_rounds is not None
        and published_round.number < discussion.max_rounds
    )
