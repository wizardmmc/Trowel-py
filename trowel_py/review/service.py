"""串联卡片数据、复习排程和统计查询。"""

import logging
from datetime import datetime, timezone
from typing import TypedDict

from trowel_py.review.models import FSRSState as FSRSState
from trowel_py.review.models import ReviewLog
from trowel_py.cards.models import Card
from trowel_py.review.repository import ReviewRepository
from trowel_py.cards.repository import CardRepository
from trowel_py.review.scheduler import get_plant_stage, schedule_review

logger = logging.getLogger(__name__)


class DueCardData(TypedDict):
    """表示一张到期卡片及其排程和植物阶段。"""

    card: Card
    fsrs_state: FSRSState
    plant_stage: str


class ReviewSubmissionData(TypedDict):
    """表示一次复习写入后的卡片、排程和展示结果。"""

    card: Card
    fsrs_state: FSRSState
    review_log: ReviewLog
    plant_stage: str
    plant_changed: bool


def get_due_cards(
    review_repo: ReviewRepository, card_repo: CardRepository
) -> list[DueCardData]:
    """返回已经到期且卡片仍然存在的复习项。"""
    now = datetime.now(timezone.utc).isoformat()
    due_states = review_repo.find_due(now)

    results: list[DueCardData] = []
    for state in due_states:
        card = card_repo.find_by_id(state.card_id)
        if card is None:
            logger.warning("FSRS state references missing card: %s", state.card_id)
            continue
        results.append(
            {
                "card": card,
                "fsrs_state": state,
                "plant_stage": get_plant_stage(state.state),
            }
        )
    return results


def submit_review(
    card_id: str, rating: int, review_repo: ReviewRepository, card_repo: CardRepository
) -> ReviewSubmissionData | None:
    """计算并保存一次卡片复习，卡片或状态不存在时返回空值。"""
    card = card_repo.find_by_id(card_id)
    if card is None:
        logger.warning("Submit review for unknown card: %s", card_id)
        return None

    state = review_repo.find_by_card_id(card_id)
    if state is None:
        logger.error("No FSRS state for card: %s", card_id)
        return None

    now = datetime.now(timezone.utc)
    new_state, review_log = schedule_review(state, rating, now)

    review_repo.update_fsrs_state(new_state)
    review_repo.save_review_log(review_log)

    return {
        "card": card,
        "fsrs_state": new_state,
        "review_log": review_log,
        "plant_stage": get_plant_stage(new_state.state),
        "plant_changed": get_plant_stage(new_state.state)
        != get_plant_stage(state.state),
    }


def get_session_stats(
    review_repo: ReviewRepository, since: str
) -> dict[str, int | float]:
    """返回指定时间之后的复习统计。"""
    return review_repo.get_session_stats(since)


def get_review_stats(review_repo: ReviewRepository) -> dict[str, int | float]:
    """返回全部历史复习统计。"""
    return review_repo.get_session_stats("2000-01-01T00:00:00")
