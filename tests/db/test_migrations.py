import sqlite3
from pathlib import Path

import pytest

from trowel_py.db.migrate import run_migrations


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "select name from sqlite_master where type = 'table'"
    ).fetchall()
    return {row["name"] for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"] for row in conn.execute(f"pragma table_info('{table}')").fetchall()
    }


def _insert_card(conn: sqlite3.Connection, card_id: str = "test-card") -> None:
    conn.execute(
        "insert into cards (id, title, category, explanation, tags) "
        "values (?, ?, ?, ?, ?)",
        (card_id, "test card", "python", "for FK tests", '["python"]'),
    )


def _insert_follow_up_thread(
    conn: sqlite3.Connection,
    thread_id: str = "thread-1",
    card_id: str = "test-card",
) -> None:
    conn.execute(
        "insert into follow_up_threads (id, card_id) values (?, ?)",
        (thread_id, card_id),
    )


def test_run_migrations_creates_tracking_table(
    db_connection: sqlite3.Connection,
    tmp_path: Path,
):
    run_migrations(db_connection, str(tmp_path))
    assert "_migrations" in _table_names(db_connection)


def test_run_migrations_is_idempotent(
    db_connection: sqlite3.Connection,
    tmp_path: Path,
):
    (tmp_path / "001_test.sql").write_text("create table foo (id integer primary key)")

    run_migrations(db_connection, migrations_dir=tmp_path)
    run_migrations(db_connection, migrations_dir=tmp_path)

    applied = db_connection.execute(
        "select count(*) as count from _migrations"
    ).fetchone()["count"]
    assert applied == 1


def test_cards_migration_creates_schema_and_fts_index(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)

    assert {"cards", "cards_fts"}.issubset(_table_names(db_connection))
    assert _column_names(db_connection, "cards") == {
        "id",
        "title",
        "category",
        "explanation",
        "example",
        "difficulty",
        "source",
        "tags",
        "status",
        "created_at",
        "updated_at",
    }

    _insert_card(db_connection, "searchable-card")
    matches = db_connection.execute(
        "select * from cards_fts where cards_fts match 'python'"
    ).fetchall()
    assert len(matches) == 1


def test_game_migrations_create_expected_tables(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)

    expected_tables = {
        "fsrs_state",
        "review_logs",
        "card_explanation_history",
        "players",
        "pets",
        "inventory",
        "event_log",
        "event_cooldowns",
        "user_preferences",
        "cold_start_answers",
    }
    assert expected_tables.issubset(_table_names(db_connection))


def test_deleting_card_cascades_to_fsrs_state(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _insert_card(db_connection)
    db_connection.execute(
        "insert into fsrs_state (card_id, state) values (?, ?)",
        ("test-card", 2),
    )

    db_connection.execute("delete from cards where id = ?", ("test-card",))

    remaining = db_connection.execute(
        "select count(*) as count from fsrs_state"
    ).fetchone()["count"]
    assert remaining == 0


def test_feynman_migration_creates_tables_and_columns(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)

    assert {
        "feynman_sessions",
        "follow_up_threads",
        "follow_up_messages",
    }.issubset(_table_names(db_connection))
    assert {
        "id",
        "card_id",
        "question",
        "accuracy",
        "completeness",
        "feedback",
        "missed_points",
    }.issubset(_column_names(db_connection, "feynman_sessions"))


def test_deleting_card_cascades_to_feynman_session(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _insert_card(db_connection)
    db_connection.execute(
        "insert into feynman_sessions (id, card_id, question, user_answer) "
        "values (?, ?, ?, ?)",
        ("session-1", "test-card", "what is a closure?", "a nested function"),
    )

    db_connection.execute("delete from cards where id = ?", ("test-card",))

    remaining = db_connection.execute(
        "select count(*) as count from feynman_sessions"
    ).fetchone()["count"]
    assert remaining == 0


def test_deleting_follow_up_thread_cascades_to_messages(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _insert_card(db_connection)
    _insert_follow_up_thread(db_connection)
    db_connection.execute(
        "insert into follow_up_messages (id, thread_id, role, content) "
        "values (?, ?, ?, ?)",
        ("message-1", "thread-1", "user", "why does the closure survive?"),
    )

    db_connection.execute(
        "delete from follow_up_threads where id = ?",
        ("thread-1",),
    )

    remaining = db_connection.execute(
        "select count(*) as count from follow_up_messages"
    ).fetchone()["count"]
    assert remaining == 0


def test_follow_up_thread_rejects_missing_card(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_follow_up_thread(db_connection, card_id="missing-card")


@pytest.mark.parametrize(
    ("statement", "invalid_value"),
    [
        (
            "insert into feynman_sessions "
            "(id, card_id, question, accuracy) values (?, ?, ?, ?)",
            150,
        ),
        (
            "insert into feynman_sessions "
            "(id, card_id, question, completeness) values (?, ?, ?, ?)",
            -1,
        ),
    ],
    ids=["accuracy", "completeness"],
)
def test_feynman_session_rejects_out_of_range_scores(
    db_connection: sqlite3.Connection,
    statement: str,
    invalid_value: int,
):
    run_migrations(db_connection)
    _insert_card(db_connection)

    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            statement,
            ("invalid-score", "test-card", "question", invalid_value),
        )


def test_follow_up_message_rejects_invalid_role(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _insert_card(db_connection)
    _insert_follow_up_thread(db_connection)

    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            "insert into follow_up_messages (id, thread_id, role, content) "
            "values (?, ?, ?, ?)",
            ("message-1", "thread-1", "system", "invalid role"),
        )


def test_feynman_accuracy_allows_null(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _insert_card(db_connection)
    db_connection.execute(
        "insert into feynman_sessions (id, card_id, question) values (?, ?, ?)",
        ("session-1", "test-card", "question"),
    )

    row = db_connection.execute(
        "select accuracy from feynman_sessions where id = ?",
        ("session-1",),
    ).fetchone()
    assert row["accuracy"] is None
