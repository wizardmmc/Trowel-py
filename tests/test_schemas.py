import pytest
from pydantic import ValidationError
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState, ReviewLog


def test_card_validates_required_fields():
    """missing required field raises ValidationError"""
    with pytest.raises(ValidationError):
        Card(id="1", title="", category="test", explanation="a valid explanation text")


def test_card_rejects_invalid_difficulty():
    """difficulty out of range raises ValidationError"""
    with pytest.raises(ValidationError):
        Card(id="1", title="Test", category="test", explanation="a valid explanation", difficulty=99)


def test_card_rejects_invalid_status():
    """status not in allowed values raises ValidationError"""
    with pytest.raises(ValidationError):
        Card(id="1", title="Test", category="test", explanation="a valid explanation", status="invalid")


def test_card_optional_fields_default():
    """optional fields default to None or their default values"""
    card = Card(id="1", title="Test", category="test", explanation="a valid explanation")
    assert card.example is None
    assert card.source is None
    assert card.tags == []
    assert card.difficulty == 3
    assert card.status == "active"


def test_review_log_rejects_invalid_rating():
    """rating out of 1-4 range raises ValidationError"""
    with pytest.raises(ValidationError):
        ReviewLog(id="1", card_id="c1", rating=5, state=0)


def test_fsrs_state_rejects_invalid_state():
    """state out of 0-3 range raises ValidationError"""
    with pytest.raises(ValidationError):
        FSRSState(card_id="c1", state=4)
