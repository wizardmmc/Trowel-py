"""Unit tests for service layer and save_fsrs_state."""
import sqlite3
from unittest.mock import MagicMock
from datetime import datetime

from trowel_py.cards.repository import create_card_repository
from trowel_py.cards.service import extract_cards, review_card, find_duplicates
from trowel_py.llm.client import LLMService
from trowel_py.review.repository import create_review_repository
from trowel_py.schemas.api import CardDraft, ReviewRequest
from trowel_py.schemas.extracted_card import ExtractedCard, ExtractOutput
from trowel_py.schemas.card import Card

import pytest


@pytest.fixture
def conn():
    """In-memory DB with cards + FTS5 + fsrs_state tables for isolated testing."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("""
        CREATE TABLE cards(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
            explanation TEXT NOT NULL, example TEXT, difficulty INTEGER DEFAULT 3,
            source TEXT, tags TEXT,
            status TEXT DEFAULT 'active' CHECK(status IN ('active','archived','draft')),
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE VIRTUAL TABLE cards_fts USING fts5(
            title, explanation, tags, content=cards, content_rowid=rowid
        )
    """)
    c.execute("""
        CREATE TRIGGER cards_fts_ai AFTER INSERT ON cards BEGIN
            INSERT INTO cards_fts(rowid, title, explanation, tags)
            VALUES (new.rowid, new.title, new.explanation, new.tags);
        END
    """)
    c.execute("""
        CREATE TABLE fsrs_state(
            card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            stability REAL DEFAULT 0, difficulty REAL DEFAULT 0,
            elapsed_days INTEGER DEFAULT 0, scheduled_days INTEGER DEFAULT 0,
            reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0,
            state INTEGER DEFAULT 0, due TEXT DEFAULT (datetime('now')),
            last_review TEXT
        )
    """)
    yield c
    c.close()


@pytest.fixture
def card_repo(conn):
    """CardRepository backed by in-memory DB."""
    return create_card_repository(conn)


@pytest.fixture
def review_repo(conn):
    """ReviewRepository backed by in-memory DB."""
    return create_review_repository(conn)


def _insert_card(card_repo, **overrides):
    """Helper: insert a card into DB with sensible defaults."""
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


# --- save_fsrs_state tests ---

class TestSaveFsrsState:
    def test_insert_state(self, conn, card_repo, review_repo):
        """Insert FSRS state for a card, verify row exists in DB."""
        _insert_card(card_repo)
        from trowel_py.schemas.review import FSRSState
        state = FSRSState(card_id="test-card-001", state=0, due=datetime.now())
        result = review_repo.save_fsrs_state(state)

        assert result.card_id == "test-card-001"
        row = conn.execute("SELECT * FROM fsrs_state WHERE card_id=?", ("test-card-001",)).fetchone()
        assert row is not None
        assert row["state"] == 0

    def test_insert_with_last_review(self, conn, card_repo, review_repo):
        """FSRS state with last_review set should persist datetime string."""
        _insert_card(card_repo)
        from trowel_py.schemas.review import FSRSState
        now = datetime.now()
        state = FSRSState(card_id="test-card-001", state=1, due=now, last_review=now)
        review_repo.save_fsrs_state(state)

        row = conn.execute("SELECT * FROM fsrs_state WHERE card_id=?", ("test-card-001",)).fetchone()
        assert row["last_review"] is not None


# --- extract_cards tests ---

class TestExtractCards:
    def test_returns_drafts_from_llm(self):
        """Mock LLM returns 1 card → extract_cards should return 1 CardDraft."""
        mock_service = MagicMock(spec=LLMService)
        mock_service.structured_call.return_value = ExtractOutput(cards=[
            ExtractedCard(
                title="Python Decorators",
                category="Python",
                explanation="A decorator wraps a function to extend its behavior",
                tags=["python"],
                confidence=4,
                source_type="git_diff",
            ),
        ])

        drafts = extract_cards("some diff text", mock_service)

        assert len(drafts) == 1
        assert drafts[0].title == "Python Decorators"
        assert len(drafts[0].id) == 12

    def test_empty_result(self):
        """Mock LLM returns 0 cards → extract_cards should return empty list."""
        mock_service = MagicMock(spec=LLMService)
        mock_service.structured_call.return_value = ExtractOutput(cards=[])

        drafts = extract_cards("nothing useful", mock_service)
        assert drafts == []


# --- review_card tests ---

class TestReviewCard:
    def _make_draft(self):
        """Helper: build a valid CardDraft for testing."""
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
        """Accept should create card in DB + initialize FSRS state."""
        draft = self._make_draft()
        request = ReviewRequest(action="accept")

        card = review_card(draft, request, card_repo, review_repo)

        assert card is not None
        assert card.title == "Python Decorators"
        assert card.status == "active"

        # verify card in DB
        db_card = card_repo.find_by_id(card.id)
        assert db_card is not None

        # verify fsrs_state in DB
        row = conn.execute("SELECT * FROM fsrs_state WHERE card_id=?", (card.id,)).fetchone()
        assert row is not None
        assert row["state"] == 0

    def test_reject_returns_none(self, card_repo, review_repo):
        """Reject should not create any card or FSRS state."""
        draft = self._make_draft()
        request = ReviewRequest(action="reject")

        result = review_card(draft, request, card_repo, review_repo)

        assert result is None
        assert card_repo.find_all() == []

    def test_edit_applies_user_changes(self, conn, card_repo, review_repo):
        """Edit should apply user edits then create card in DB."""
        draft = self._make_draft()
        request = ReviewRequest(action="edit", edits={"title": "Decorators in Python"})

        card = review_card(draft, request, card_repo, review_repo)

        assert card is not None
        assert card.title == "Decorators in Python"

        db_card = card_repo.find_by_id(card.id)
        assert db_card.title == "Decorators in Python"


# --- find_duplicates tests ---

class TestFindDuplicates:
    def test_exact_title_match(self, card_repo):
        """Card with exact same title should appear in duplicates."""
        _insert_card(card_repo, title="Python Decorators")

        results = find_duplicates("Python Decorators", card_repo)

        assert len(results) == 1
        assert results[0].title == "Python Decorators"

    def test_no_duplicates(self, card_repo):
        """Different title should return empty list."""
        _insert_card(card_repo, title="Java Streams")

        results = find_duplicates("Python Decorators", card_repo)

        assert results == []

    def test_fts5_fuzzy_match(self, card_repo):
        """FTS5 should find cards with similar titles."""
        _insert_card(card_repo, title="Python Decorators Introduction")

        results = find_duplicates("Python Decorators", card_repo)

        assert len(results) >= 1

    def test_no_duplicate_results(self, card_repo):
        """Exact match and FTS5 might return same card — should appear only once."""
        _insert_card(card_repo, title="Python Decorators")

        results = find_duplicates("Python Decorators", card_repo)
        ids = [c.id for c in results]

        assert len(ids) == len(set(ids))
