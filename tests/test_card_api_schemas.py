"""Tests for api.py schemas — data validation layer."""
import pytest
from pydantic import ValidationError
from trowel_py.schemas.api import ExtractRequest, CardDraft, ReviewRequest


class TestExtractRequest:
    def test_valid_content(self):
        """Valid content string should create ExtractRequest successfully."""
        req = ExtractRequest(content="some diff text")
        assert req.content == "some diff text"

    def test_empty_content_rejected(self):
        """Empty string should fail min_length=1 validation."""
        with pytest.raises(ValidationError):
            ExtractRequest(content="")

    def test_whitespace_only_passes(self):
        """Pydantic min_length counts chars, doesn't strip whitespace."""
        req = ExtractRequest(content="   ")
        assert req.content == "   "


class TestCardDraft:
    def _make_draft(self, **overrides):
        """Helper to build a valid CardDraft with sensible defaults."""
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

    def test_valid_draft(self):
        """All required fields present should create CardDraft."""
        draft = self._make_draft()
        assert draft.id == "abc123"
        assert draft.title == "Python Decorators"

    def test_optional_fields_default_none(self):
        """example and source should default to None when not provided."""
        draft = self._make_draft()
        assert draft.example is None
        assert draft.source is None

    def test_empty_title_rejected(self):
        """Empty title should fail min_length=1 validation."""
        with pytest.raises(ValidationError):
            self._make_draft(title="")

    def test_explanation_too_short_rejected(self):
        """Explanation shorter than min_length=10 should be rejected."""
        with pytest.raises(ValidationError):
            self._make_draft(explanation="short")

    def test_difficulty_out_of_range_rejected(self):
        """Difficulty outside 1-5 range should be rejected."""
        with pytest.raises(ValidationError):
            self._make_draft(difficulty=6)
        with pytest.raises(ValidationError):
            self._make_draft(difficulty=0)


class TestReviewRequest:
    def test_accept(self):
        """Valid accept action should create ReviewRequest."""
        req = ReviewRequest(action="accept")
        assert req.action == "accept"

    def test_reject(self):
        """Reject action should have no edits by default."""
        req = ReviewRequest(action="reject")
        assert req.edits is None

    def test_edit_with_edits(self):
        """Edit action with edits dict should preserve values."""
        req = ReviewRequest(action="edit", edits={"title": "New Title"})
        assert req.edits["title"] == "New Title"

    def test_invalid_action_rejected(self):
        """Action not in Literal["accept","edit","reject"] should be rejected."""
        with pytest.raises(ValidationError):
            ReviewRequest(action="maybe")
