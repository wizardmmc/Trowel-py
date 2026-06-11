"""
FSRS scheduler wrapper - bridges between our FSRSState schema with the fsrs library
"""
from datetime import datetime, timezone
import fsrs
from trowel_py.schemas.review import FSRSState, ReviewLog

# module-level constant
_SCHEDULER = fsrs.Scheduler()

_RATING_MAP = {
    1: fsrs.Rating.Again,
    2: fsrs.Rating.Hard,
    3: fsrs.Rating.Good,
    4: fsrs.Rating.Easy
}   # can't have 5 because fsrs library not set 5? yes

_PLANT_STAGES = {
    0: "seed",
    1: "sprout",
    2: "tree",
    3: "wilting"
}

def schedule_review(state: FSRSState, rating: int, now: datetime | None = None) -> tuple[FSRSState, ReviewLog]:
    """
    coumpute the new fsrs state after a review

    Args:
        state: current FSRS state from datebase
        rating: 1=Again, 2=Hard, 3= Good, 4=Easy
        now: review timestamp (defaults to utcnow)

    Returns:
        (new_state, review_log) - both ready to persist
    """
    if now is None:
        now = datetime.now(timezone.utc)

    card = _state_to_card(state)
    fsrs_rating = _RATING_MAP[rating]

    new_card, fsrs_log = _SCHEDULER.review_card(card, fsrs_rating, now)
    new_state = _card_to_state(new_card, state.card_id, state.reps, state.lapses, rating)

    # calculate elapsed_days and scheduled_days
    if state.last_review is not None:
        last = state.last_review.replace(tzinfo=timezone.utc) if state.last_review.tzinfo is None else state.last_review
        elapsed_days = (now - last).days
    else:
        elapsed_days = 0
    scheduled = (new_card.due - now).days

    review_log = ReviewLog(
        id=str(fsrs_log.card_id),
        card_id=state.card_id,
        rating=rating,
        state=new_state.state,
        elapsed_days=max(elapsed_days, 0),
        scheduled_days=max(scheduled, 0),
        created_at=now,
    )

    return new_state, review_log

def get_plant_stage(state: int) -> str:
    """
    Map FSRS state number to a plant stage name.
    """
    return _PLANT_STAGES.get(state, "seed")

def _state_to_card(state: FSRSState) -> fsrs.Card:
    """
    convert our FSRSState to fsrs.Card for the library to process.
    """
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
        ),  # DB don't have time zone infomation, but fsrs library needs
    )

def _card_to_state(card: fsrs.Card, card_id: str, prev_reps: int, prev_lapses: int, rating: int) -> FSRSState:
    """
    convert fsrs.Card to FSRSState for storage
    """
    # record this time whether user is forget the content
    is_lapse = rating == 1 and card.state == fsrs.State.Relearning
    return FSRSState(
        card_id=card_id,
        stability=card.stability if card.stability is not None else 0.0,
        difficulty=card.difficulty if card.difficulty is not None else 0.0,
        elapsed_days=0,
        scheduled_days=0,
        reps=prev_reps+1,
        lapses=prev_lapses+(1 if is_lapse else 0),
        state=_fsrs_to_our_state(card.state),
        due=card.due,
        last_review=card.last_review
    )

def _our_state_to_fsrs(state: int) -> fsrs.State:
    """
    Our DB uses 0:new, 1:learning, 2:review, 3:relearning
    fsrs.State starts at 1:learning. state 0 has no fsrs equivalent.
    """
    mapping = {
        0: fsrs.State.Learning, # new -> treat as learning for the library
        1: fsrs.State.Learning,
        2: fsrs.State.Review,
        3: fsrs.State.Relearning
    }
    return mapping.get(state, fsrs.State.Learning)

def _fsrs_to_our_state(state: fsrs.State) -> int:
    mapping = {
        fsrs.State.Learning: 1,
        fsrs.State.Review: 2,
        fsrs.State.Relearning: 3,
    }
    return mapping.get(state, 1)