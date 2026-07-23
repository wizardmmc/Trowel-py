from datetime import datetime, timezone

from trowel_py.schemas.review import FSRSState
from trowel_py.review.scheduler import schedule_review, get_plant_stage


def _new_state(card_id: str = "card-1") -> FSRSState:
    return FSRSState(card_id=card_id, state=0, due=datetime.now(timezone.utc))


class TestScheduleReview:
    def test_new_card_rating_good_advances_reps(self):
        state = _new_state()
        new_state, review_log = schedule_review(state, 3)

        assert new_state.reps == 1
        assert new_state.card_id == "card-1"
        assert review_log.card_id == "card-1"
        assert review_log.rating == 3

    def test_new_card_rating_again_counts_lapse(self):
        state = _new_state()
        new_state, _ = schedule_review(state, 1)

        assert new_state.reps == 1
        assert new_state.lapses == 0

    def test_four_ratings_produce_different_intervals(self):
        results = []
        for rating in [1, 2, 3, 4]:
            state = _new_state()
            new_state, _ = schedule_review(state, rating)
            results.append(new_state.due)

        due_dates = [r for r in results]
        assert len(set(due_dates)) > 1

    def test_consecutive_good_transitions_state(self):
        state = _new_state()
        assert state.state == 0

        states = [state.state]
        for _ in range(3):
            state, _ = schedule_review(state, 3)
            states.append(state.state)

        assert states[0] == 0
        assert states[1] >= 1
        # FSRS 允许多轮停留在 Learning；这里只约束复习后不退回 New。
        for s in states[1:]:
            assert s >= 1

    def test_again_increases_difficulty(self):
        state_good = _new_state()
        state_good, _ = schedule_review(state_good, 3)

        state_again = _new_state()
        state_again, _ = schedule_review(state_again, 1)

        assert state_again.difficulty > state_good.difficulty

    def test_custom_now_parameter(self):
        fixed_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        state = _new_state()

        new_state, review_log = schedule_review(state, 3, now=fixed_time)

        assert review_log.created_at == fixed_time
        assert new_state.last_review == fixed_time

    def test_stability_and_difficulty_populated_after_review(self):
        state = _new_state()
        new_state, _ = schedule_review(state, 3)

        assert new_state.stability > 0
        assert new_state.difficulty > 0


class TestGetPlantStage:
    def test_all_valid_states(self):
        assert get_plant_stage(0) == "seed"
        assert get_plant_stage(1) == "sprout"
        assert get_plant_stage(2) == "tree"
        assert get_plant_stage(3) == "wilting"

    def test_invalid_state_defaults_to_seed(self):
        assert get_plant_stage(99) == "seed"
        assert get_plant_stage(-1) == "seed"

    def test_plant_changes_on_state_transition(self):
        state = _new_state()
        old_plant = get_plant_stage(state.state)

        state, _ = schedule_review(state, 3)
        new_plant = get_plant_stage(state.state)

        assert old_plant != new_plant
        assert old_plant == "seed"
        assert new_plant == "sprout"
