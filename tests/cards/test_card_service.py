import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, call

import pytest

from trowel_py.cards.repository import CardRepository, create_card_repository
from trowel_py.cards.service import extract_cards, review_card, find_duplicates
from trowel_py.db.migrate import run_migrations
from trowel_py.llm.client import LLMService
from trowel_py.review.repository import ReviewRepository, create_review_repository
from trowel_py.schemas.api import CardDraft, ReviewRequest
from trowel_py.schemas.card import Card
from trowel_py.schemas.extracted_card import ExtractedCard, ExtractOutput
from trowel_py.schemas.review import FSRSState


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    run_migrations(c)
    yield c
    c.close()


@pytest.fixture
def card_repo(conn):
    return create_card_repository(conn)


@pytest.fixture
def review_repo(conn):
    return create_review_repository(conn)


def _insert_card(card_repo, **overrides):
    defaults = {
        "id": "test-card-001",
        "title": "Python Decorators",
        "category": "Python",
        "explanation": "A decorator wraps a function to extend its behavior without modifying it",
        "difficulty": 3,
        "tags": ["python"],
        "status": "active",
    }
    defaults.update(overrides)
    card = Card(**defaults)
    card_repo.create(card)
    return card


class TestSaveFsrsState:
    def test_insert_state(self, conn, card_repo, review_repo):
        _insert_card(card_repo)

        state = FSRSState(card_id="test-card-001", state=0, due=datetime.now())
        result = review_repo.save_fsrs_state(state)

        assert result.card_id == "test-card-001"
        row = conn.execute(
            "SELECT * FROM fsrs_state WHERE card_id=?", ("test-card-001",)
        ).fetchone()
        assert row is not None
        assert row["state"] == 0

    def test_insert_with_last_review(self, conn, card_repo, review_repo):
        _insert_card(card_repo)

        now = datetime.now()
        state = FSRSState(card_id="test-card-001", state=1, due=now, last_review=now)
        review_repo.save_fsrs_state(state)

        row = conn.execute(
            "SELECT * FROM fsrs_state WHERE card_id=?", ("test-card-001",)
        ).fetchone()
        assert row["last_review"] is not None


class TestExtractCards:
    def test_returns_drafts_from_llm(self):
        mock_service = MagicMock(spec=LLMService)
        mock_service.structured_call.return_value = ExtractOutput(
            cards=[
                ExtractedCard(
                    title="Python Decorators",
                    category="Python",
                    explanation="A decorator wraps a function to extend its behavior",
                    tags=["python"],
                    confidence=4,
                    source_type="git_diff",
                ),
            ]
        )

        drafts = extract_cards("some diff text", mock_service)

        assert len(drafts) == 1
        assert drafts[0].title == "Python Decorators"
        assert len(drafts[0].id) == 12

    def test_returns_empty_when_llm_finds_no_cards(self):
        mock_service = MagicMock(spec=LLMService)
        mock_service.structured_call.return_value = ExtractOutput(cards=[])

        drafts = extract_cards("nothing useful", mock_service)
        assert drafts == []

    def test_preserves_extracted_fields_and_maps_source(self):
        mock_service = MagicMock(spec=LLMService)
        mock_service.structured_call.return_value = ExtractOutput(
            cards=[
                ExtractedCard(
                    title="Python Decorators",
                    category="Python",
                    explanation="A decorator wraps a function to extend its behavior",
                    example="@cache",
                    difficulty=4,
                    tags=["python", "functions"],
                    confidence=5,
                    source_type="git_diff",
                ),
                ExtractedCard(
                    title="Context Managers",
                    category="Python",
                    explanation="A context manager controls resource cleanup",
                    tags=["python"],
                    source_type="chat",
                ),
            ]
        )

        drafts = extract_cards("some diff text", mock_service)

        assert [draft.model_dump(exclude={"id"}) for draft in drafts] == [
            {
                "title": "Python Decorators",
                "category": "Python",
                "explanation": ("A decorator wraps a function to extend its behavior"),
                "example": "@cache",
                "difficulty": 4,
                "tags": ["python", "functions"],
                "confidence": 5,
                "source_type": "git_diff",
                "source": "git_diff",
            },
            {
                "title": "Context Managers",
                "category": "Python",
                "explanation": "A context manager controls resource cleanup",
                "example": None,
                "difficulty": 3,
                "tags": ["python"],
                "confidence": 3,
                "source_type": "chat",
                "source": "chat",
            },
        ]
        assert drafts[0].id != drafts[1].id


class TestReviewCard:
    def _make_draft(self):
        return CardDraft(
            id="draft-001",
            title="Python Decorators",
            category="Python",
            explanation="A decorator wraps a function to extend its behavior",
            difficulty=3,
            tags=["python"],
            confidence=4,
            source_type="git_diff",
        )

    def test_accept_creates_card_and_fsrs(self, conn, card_repo, review_repo):
        draft = self._make_draft()
        request = ReviewRequest(action="accept")

        card = review_card(draft, request, card_repo, review_repo)

        assert card is not None
        assert card.title == "Python Decorators"
        assert card.status == "active"

        db_card = card_repo.find_by_id(card.id)
        assert db_card is not None

        row = conn.execute(
            "SELECT * FROM fsrs_state WHERE card_id=?", (card.id,)
        ).fetchone()
        assert row is not None
        assert row["state"] == 0

    def test_reject_returns_none(self, card_repo, review_repo):
        draft = self._make_draft()
        request = ReviewRequest(action="reject")

        result = review_card(draft, request, card_repo, review_repo)

        assert result is None
        assert card_repo.find_all() == []

    def test_reject_does_not_call_repositories(self):
        draft = self._make_draft()
        card_repo = MagicMock(spec=CardRepository)
        review_repo = MagicMock(spec=ReviewRepository)

        result = review_card(
            draft,
            ReviewRequest(action="reject"),
            card_repo,
            review_repo,
        )

        assert result is None
        card_repo.create.assert_not_called()
        review_repo.save_fsrs_state.assert_not_called()

    def test_edit_applies_user_changes(self, conn, card_repo, review_repo):
        draft = self._make_draft()
        request = ReviewRequest(action="edit", edits={"title": "Decorators in Python"})

        card = review_card(draft, request, card_repo, review_repo)

        assert card is not None
        assert card.title == "Decorators in Python"

        db_card = card_repo.find_by_id(card.id)
        assert db_card.title == "Decorators in Python"

    def test_accept_uses_new_persistent_id_and_saves_card_before_state(self):
        draft = self._make_draft()
        operations = MagicMock()
        card_repo = MagicMock()
        review_repo = MagicMock()
        operations.attach_mock(card_repo, "cards")
        operations.attach_mock(review_repo, "reviews")

        card = review_card(
            draft,
            ReviewRequest(action="accept"),
            card_repo,
            review_repo,
        )

        assert card is not None
        assert card.id != draft.id
        saved_state = review_repo.save_fsrs_state.call_args.args[0]
        assert saved_state.card_id == card.id
        assert operations.mock_calls == [
            call.cards.create(card),
            call.reviews.save_fsrs_state(saved_state),
        ]


class TestFindDuplicates:
    def test_exact_title_match(self, card_repo):
        _insert_card(card_repo, title="Python Decorators")

        results = find_duplicates("Python Decorators", card_repo)

        assert len(results) == 1
        assert results[0].title == "Python Decorators"

    def test_no_duplicates(self, card_repo):
        _insert_card(card_repo, title="Java Streams")

        results = find_duplicates("Python Decorators", card_repo)

        assert results == []

    def test_fts5_fuzzy_match(self, card_repo):
        _insert_card(card_repo, title="Python Decorators Introduction")

        results = find_duplicates("Python Decorators", card_repo)

        assert len(results) >= 1

    def test_no_duplicate_results(self, card_repo):
        _insert_card(card_repo, title="Python Decorators")

        results = find_duplicates("Python Decorators", card_repo)
        ids = [c.id for c in results]

        assert len(ids) == len(set(ids))

    def test_exact_matches_precede_unique_fts_results(self):
        exact = Card(
            id="exact",
            title="Python Decorators",
            category="Python",
            explanation="A decorator wraps a function to extend its behavior",
        )
        fuzzy = Card(
            id="fuzzy",
            title="Python Decorators Introduction",
            category="Python",
            explanation="An introduction to decorators and wrapped functions",
        )
        card_repo = MagicMock()
        card_repo.find_all.return_value = [fuzzy, exact]
        card_repo.search_by_fts5.return_value = [exact, fuzzy]

        results = find_duplicates("Python Decorators", card_repo)

        assert results == [exact, fuzzy]
