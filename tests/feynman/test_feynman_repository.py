"""Tests for FeynmanRepository (slice 019).

Bottom of the pyramid: repo gets the most tests. Uses an in-memory sqlite
db (conftest.db_connection) with migrations run per test, and a seeded parent
card so FK-referencing sessions can be created.
"""
import sqlite3

import pytest

from trowel_py.db.migrate import run_migrations
from trowel_py.feynman.repository import (
    FeynmanSession,
    create_feynman_repository,
)


def _seed_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    """insert a parent card so FK-referencing sessions can be created."""
    conn.execute(
        "insert into cards (id, title, category, explanation, tags) "
        "values (?, ?, ?, ?, ?)",
        (card_id, "test card", "python", "for FK tests", '["python"]'),
    )


def _session(
    session_id: str,
    card_id: str = "card-1",
    question: str = "explain X",
) -> FeynmanSession:
    """build a question-stage session with sensible defaults; override per test."""
    return FeynmanSession(
        id=session_id,
        card_id=card_id,
        question=question,
    )


# --- create + read back (state machine: question-generated stage) ---


def test_create_then_find_by_id_has_null_eval_fields(
    db_connection: sqlite3.Connection,
):
    """a freshly created session has the question but no answer/scores yet."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)

    repo.create(_session("s-1", question="why X?"))

    session = repo.find_by_id("s-1")
    assert session is not None
    assert session.question == "why X?"
    assert session.card_id == "card-1"
    # question-generated stage: eval fields still null
    assert session.user_answer is None
    assert session.accuracy is None
    assert session.completeness is None
    assert session.feedback is None
    assert session.missed_points is None
    # DB filled created_at via default
    assert session.created_at is not None


# --- update_with_evaluation (state machine: evaluated stage) ---


def test_update_with_evaluation_fills_fields_and_roundtrips_missed_points(
    db_connection: sqlite3.Connection,
):
    """after update, eval fields are filled and missed_points survives list<->text."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)
    repo.create(_session("s-1"))

    repo.update_with_evaluation(
        session_id="s-1",
        user_answer="my answer",
        accuracy=80,
        completeness=70,
        feedback="good but missed Y",
        missed_points=["point Y", "point Z"],
    )

    session = repo.find_by_id("s-1")
    assert session is not None
    assert session.user_answer == "my answer"
    assert session.accuracy == 80
    assert session.completeness == 70
    assert session.feedback == "good but missed Y"
    # list<->text roundtrip: stored as JSON text, read back as list
    assert session.missed_points == ["point Y", "point Z"]


def test_update_with_evaluation_empty_missed_points_roundtrips(
    db_connection: sqlite3.Connection,
):
    """an empty missed_points list must roundtrip to [], not None."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)
    repo.create(_session("s-1"))

    repo.update_with_evaluation(
        session_id="s-1",
        user_answer="full answer",
        accuracy=100,
        completeness=100,
        feedback="perfect",
        missed_points=[],
    )

    session = repo.find_by_id("s-1")
    assert session is not None
    assert session.missed_points == []


def test_update_with_evaluation_nonexistent_session_raises(
    db_connection: sqlite3.Connection,
):
    """updating a session that doesn't exist raises (rowcount check, fail-fast)."""
    run_migrations(db_connection)
    repo = create_feynman_repository(db_connection)

    with pytest.raises(RuntimeError):
        repo.update_with_evaluation(
            session_id="ghost-session",
            user_answer="x",
            accuracy=50,
            completeness=50,
            feedback="x",
            missed_points=[],
        )


def test_update_with_evaluation_accuracy_out_of_range_raises(
    db_connection: sqlite3.Connection,
):
    """accuracy > 100 violates the CHECK constraint (0-100)."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)
    repo.create(_session("s-1"))

    with pytest.raises(sqlite3.IntegrityError):
        repo.update_with_evaluation(
            session_id="s-1",
            user_answer="x",
            accuracy=101,
            completeness=50,
            feedback="x",
            missed_points=[],
        )


# --- find_by_card_id (history) ---


def test_find_by_card_id_orders_newest_first(db_connection: sqlite3.Connection):
    """multiple sessions come back ordered by created_at descending."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)

    repo.create(_session("s-1", question="old"))
    db_connection.execute(
        "UPDATE feynman_sessions SET created_at = '2026-01-01 10:00:00' WHERE id = 's-1'"
    )
    repo.create(_session("s-2", question="new"))
    db_connection.execute(
        "UPDATE feynman_sessions SET created_at = '2026-01-02 10:00:00' WHERE id = 's-2'"
    )

    sessions = repo.find_by_card_id("card-1")
    assert [s.id for s in sessions] == ["s-2", "s-1"], "newest first"


def test_find_by_card_id_empty_for_card_with_no_sessions(
    db_connection: sqlite3.Connection,
):
    """a card with no sessions: find_by_card_id -> []."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)

    assert repo.find_by_card_id("card-1") == []


def test_find_by_id_nonexistent_returns_none(db_connection: sqlite3.Connection):
    """an unknown session id: find_by_id -> None."""
    run_migrations(db_connection)
    repo = create_feynman_repository(db_connection)

    assert repo.find_by_id("ghost") is None


# --- create constraints ---


def test_create_fk_violation_raises(db_connection: sqlite3.Connection):
    """inserting a session for a non-existent card must raise (FK enforced)."""
    run_migrations(db_connection)
    repo = create_feynman_repository(db_connection)
    # no card seeded, so 'ghost-card' violates the FK
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_session("s-1", card_id="ghost-card"))


def test_create_duplicate_id_raises(db_connection: sqlite3.Connection):
    """inserting a duplicate session id must raise (primary key conflict)."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)

    repo.create(_session("s-1"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_session("s-1"))


# --- SQL injection (parameterized queries) ---


def test_find_by_card_id_safe_with_sql_in_card_id(db_connection: sqlite3.Connection):
    """a card_id containing SQL must not break the query or leak rows."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)
    repo.create(_session("s-1"))

    # malicious card_id: must be treated as a literal string, not SQL
    sessions = repo.find_by_card_id("' OR 1=1 --")
    assert sessions == []  # no leak — the literal matched nothing
