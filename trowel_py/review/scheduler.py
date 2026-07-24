"""项目复习状态与 py-fsrs 的转换边界。"""

from datetime import datetime, timezone
from typing import cast

import fsrs

from trowel_py.review.models import (
    FSRSState,
    FSRSStateCode,
    ReviewLog,
    ReviewRating,
)

# 复用调度器，避免每次复习重新构造参数集合。
_SCHEDULER = fsrs.Scheduler()

_RATING_MAP = {
    1: fsrs.Rating.Again,
    2: fsrs.Rating.Hard,
    3: fsrs.Rating.Good,
    4: fsrs.Rating.Easy,
}

_PLANT_STAGES = {
    0: "seed",
    1: "sprout",
    2: "tree",
    3: "wilting",
}


def schedule_review(
    state: FSRSState, rating: int, now: datetime | None = None
) -> tuple[FSRSState, ReviewLog]:
    """非法 `rating` 保持字典查找的 `KeyError` 语义。"""
    if now is None:
        now = datetime.now(timezone.utc)

    card = _state_to_card(state)
    fsrs_rating = _RATING_MAP[rating]
    # 字典查找成功后，rating 已满足 ReviewRating 的值域。
    review_rating = cast(ReviewRating, rating)

    new_card, fsrs_log = _SCHEDULER.review_card(card, fsrs_rating, now)
    new_state = _card_to_state(
        new_card,
        state.card_id,
        state.reps,
        state.lapses,
        review_rating,
    )

    # 旧数据可能缺少时区；按既有约定将其解释为 UTC。
    if state.last_review is not None:
        last = (
            state.last_review.replace(tzinfo=timezone.utc)
            if state.last_review.tzinfo is None
            else state.last_review
        )
        elapsed_days = (now - last).days
    else:
        elapsed_days = 0
    scheduled = (new_card.due - now).days

    review_log = ReviewLog(
        id=str(fsrs_log.card_id),
        card_id=state.card_id,
        rating=review_rating,
        state=new_state.state,
        elapsed_days=max(elapsed_days, 0),
        scheduled_days=max(scheduled, 0),
        created_at=now,
    )

    return new_state, review_log


def get_plant_stage(state: int) -> str:
    """未知状态按新卡回退为 `seed`。"""
    return _PLANT_STAGES.get(state, "seed")


def _state_to_card(state: FSRSState) -> fsrs.Card:
    """`reps == 0` 时忽略其余持久化字段，按 py-fsrs 新卡构造。"""
    if state.reps == 0:
        return fsrs.Card()

    fsrs_state_value = _our_state_to_fsrs(state.state)
    return fsrs.Card(
        state=fsrs_state_value,
        stability=state.stability,
        difficulty=state.difficulty,
        due=state.due if state.due.tzinfo else state.due.replace(tzinfo=timezone.utc),
        last_review=(
            state.last_review.replace(tzinfo=timezone.utc)
            if state.last_review and not state.last_review.tzinfo
            else state.last_review
        ),
    )


def _card_to_state(
    card: fsrs.Card,
    card_id: str,
    prev_reps: int,
    prev_lapses: int,
    rating: ReviewRating,
) -> FSRSState:
    # 只有遗忘后进入 Relearning 才累计一次 lapse。
    is_lapse = rating == 1 and card.state == fsrs.State.Relearning
    return FSRSState(
        card_id=card_id,
        stability=card.stability if card.stability is not None else 0.0,
        difficulty=card.difficulty if card.difficulty is not None else 0.0,
        elapsed_days=0,
        scheduled_days=0,
        reps=prev_reps + 1,
        lapses=prev_lapses + (1 if is_lapse else 0),
        state=_fsrs_to_our_state(card.state),
        due=card.due,
        last_review=card.last_review,
    )


def _our_state_to_fsrs(state: FSRSStateCode) -> fsrs.State:
    """数据库的 New(0) 在 py-fsrs 中无对应值，按 Learning 映射。"""
    mapping = {
        0: fsrs.State.Learning,
        1: fsrs.State.Learning,
        2: fsrs.State.Review,
        3: fsrs.State.Relearning,
    }
    return mapping.get(state, fsrs.State.Learning)


def _fsrs_to_our_state(state: fsrs.State) -> FSRSStateCode:
    mapping: dict[fsrs.State, FSRSStateCode] = {
        fsrs.State.Learning: 1,
        fsrs.State.Review: 2,
        fsrs.State.Relearning: 3,
    }
    return mapping.get(state, 1)
