import pytest
from pydantic import ValidationError
from trowel_py.schemas.api import ExtractRequest, CardDraft, ReviewRequest


class TestExtractRequest:
    def test_accepts_non_empty_content(self):
        req = ExtractRequest(content="some diff text")
        assert req.content == "some diff text"

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            ExtractRequest(content="")

    def test_whitespace_only_passes(self):
        req = ExtractRequest(content="   ")
        assert req.content == "   "


class TestCardDraft:
    def _make_draft(self, **overrides):
        defaults = {
            "id": "abc123",
            "title": "Python Decorators",
            "category": "Python",
            "explanation": "A decorator wraps a function to extend its behavior",
            "difficulty": 3,
            "tags": ["python", "functions"],
            "confidence": 4,
            "source_type": "git_diff",
        }
        defaults.update(overrides)
        return CardDraft(**defaults)

    def test_accepts_required_draft_fields(self):
        draft = self._make_draft()
        assert draft.id == "abc123"
        assert draft.title == "Python Decorators"

    def test_optional_fields_default_none(self):
        draft = self._make_draft()
        assert draft.example is None
        assert draft.source is None

    def test_empty_title_rejected(self):
        with pytest.raises(ValidationError):
            self._make_draft(title="")

    def test_explanation_too_short_rejected(self):
        with pytest.raises(ValidationError):
            self._make_draft(explanation="short")

    def test_difficulty_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            self._make_draft(difficulty=6)
        with pytest.raises(ValidationError):
            self._make_draft(difficulty=0)


class TestReviewRequest:
    def test_accept_action_is_valid(self):
        req = ReviewRequest(action="accept")
        assert req.action == "accept"

    def test_reject_action_needs_no_edits(self):
        req = ReviewRequest(action="reject")
        assert req.edits is None

    def test_edit_with_edits(self):
        req = ReviewRequest(action="edit", edits={"title": "New Title"})
        assert req.edits["title"] == "New Title"

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            ReviewRequest(action="maybe")
