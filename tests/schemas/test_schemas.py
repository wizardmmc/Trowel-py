import pytest
from pydantic import ValidationError
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState, ReviewLog


def test_card_validates_required_fields():
    with pytest.raises(ValidationError):
        Card(id="1", title="", category="test", explanation="a valid explanation text")


def test_card_rejects_invalid_difficulty():
    with pytest.raises(ValidationError):
        Card(
            id="1",
            title="Test",
            category="test",
            explanation="a valid explanation",
            difficulty=99,
        )


def test_card_rejects_invalid_status():
    with pytest.raises(ValidationError):
        Card(
            id="1",
            title="Test",
            category="test",
            explanation="a valid explanation",
            status="invalid",
        )


def test_card_optional_fields_default():
    card = Card(
        id="1", title="Test", category="test", explanation="a valid explanation"
    )
    assert card.example is None
    assert card.source is None
    assert card.tags == []
    assert card.difficulty == 3
    assert card.status == "active"


def test_review_log_rejects_invalid_rating():
    with pytest.raises(ValidationError):
        ReviewLog(id="1", card_id="c1", rating=5, state=0)


def test_fsrs_state_rejects_invalid_state():
    with pytest.raises(ValidationError):
        FSRSState(card_id="c1", state=4)
