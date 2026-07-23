import sqlite3

import pytest

from trowel_py.db.migrate import run_migrations
from trowel_py.feynman.repository import (
    FeynmanSession,
    create_feynman_repository,
)


def _seed_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
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
    return FeynmanSession(
        id=session_id,
        card_id=card_id,
        question=question,
    )


def test_create_then_find_by_id_has_null_eval_fields(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)

    repo.create(_session("s-1", question="why X?"))

    session = repo.find_by_id("s-1")
    assert session is not None
    assert session.question == "why X?"
    assert session.card_id == "card-1"
    assert session.user_answer is None
    assert session.accuracy is None
    assert session.completeness is None
    assert session.feedback is None
    assert session.missed_points is None
    assert session.created_at is not None


def test_update_with_evaluation_fills_fields_and_roundtrips_missed_points(
    db_connection: sqlite3.Connection,
):
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
    assert session.missed_points == ["point Y", "point Z"]


def test_update_with_evaluation_empty_missed_points_roundtrips(
    db_connection: sqlite3.Connection,
):
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


def test_find_by_card_id_orders_newest_first(db_connection: sqlite3.Connection):
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
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)

    assert repo.find_by_card_id("card-1") == []


def test_find_by_id_nonexistent_returns_none(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_feynman_repository(db_connection)

    assert repo.find_by_id("ghost") is None


def test_create_fk_violation_raises(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_feynman_repository(db_connection)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_session("s-1", card_id="ghost-card"))


def test_create_duplicate_id_raises(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)

    repo.create(_session("s-1"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_session("s-1"))


def test_find_by_card_id_safe_with_sql_in_card_id(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_feynman_repository(db_connection)
    repo.create(_session("s-1"))

    sessions = repo.find_by_card_id("' OR 1=1 --")
    assert sessions == []
