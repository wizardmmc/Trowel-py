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


def test_review_state_and_rating_json_schema_contract():
    fsrs_properties = FSRSState.model_json_schema()["properties"]
    review_properties = ReviewLog.model_json_schema()["properties"]

    assert fsrs_properties["state"] == {
        "default": 0,
        "enum": [0, 1, 2, 3],
        "title": "State",
        "type": "integer",
    }
    assert review_properties["state"] == {
        "enum": [0, 1, 2, 3],
        "title": "State",
        "type": "integer",
    }
    assert review_properties["rating"] == {
        "enum": [1, 2, 3, 4],
        "title": "Rating",
        "type": "integer",
    }
