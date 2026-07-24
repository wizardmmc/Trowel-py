import sqlite3
from pathlib import Path

import pytest

from trowel_py.db.connection import create_db
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

    run_migrations(db_connection, migrations_dir=str(tmp_path))
    run_migrations(db_connection, migrations_dir=str(tmp_path))

    applied = db_connection.execute(
        "select count(*) as count from _migrations"
    ).fetchone()["count"]
    assert applied == 1


def test_run_migrations_applies_only_sql_files_in_filename_order(
    db_connection: sqlite3.Connection,
    tmp_path: Path,
):
    (tmp_path / "002_append.sql").write_text(
        "insert into migration_order (position) values (2)"
    )
    (tmp_path / "001_create.sql").write_text(
        "create table migration_order (position integer);"
        "insert into migration_order (position) values (1)"
    )
    (tmp_path / "003_ignore.txt").write_text("this is not valid SQL")

    run_migrations(db_connection, migrations_dir=str(tmp_path))

    positions = db_connection.execute(
        "select position from migration_order order by rowid"
    ).fetchall()
    applied = db_connection.execute(
        "select name from _migrations order by name"
    ).fetchall()
    assert [row["position"] for row in positions] == [1, 2]
    assert [row["name"] for row in applied] == [
        "001_create.sql",
        "002_append.sql",
    ]


def test_run_migrations_persists_tracking_across_reopen(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_create.sql").write_text(
        "create table restart_safe (id integer primary key)"
    )
    db_path = tmp_path / "restart.db"

    conn = create_db(str(db_path))
    run_migrations(conn, migrations_dir=str(migrations_dir))
    conn.close()

    reopened = create_db(str(db_path))
    try:
        applied = reopened.execute(
            "select name from _migrations order by name"
        ).fetchall()
        run_migrations(reopened, migrations_dir=str(migrations_dir))
    finally:
        reopened.close()

    assert [row["name"] for row in applied] == ["001_create.sql"]


def test_run_migrations_rolls_back_partial_schema_on_failure(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_broken.sql").write_text(
        "create table partial_schema (id integer primary key);"
        "insert into missing_table (id) values (1)"
    )
    db_path = tmp_path / "failure.db"

    conn = create_db(str(db_path))
    with pytest.raises(sqlite3.OperationalError):
        run_migrations(conn, migrations_dir=str(migrations_dir))
    conn.close()

    reopened = create_db(str(db_path))
    try:
        tables = _table_names(reopened)
        applied = reopened.execute("select name from _migrations").fetchall()
    finally:
        reopened.close()

    assert "partial_schema" not in tables
    assert applied == []


def test_run_migrations_quotes_tracking_name(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration_name = "001_owner's_table.sql"
    (migrations_dir / migration_name).write_text(
        "create table quoted_name (id integer primary key)"
    )
    db_path = tmp_path / "quoted.db"

    conn = create_db(str(db_path))
    try:
        run_migrations(conn, migrations_dir=str(migrations_dir))
        applied = conn.execute("select name from _migrations").fetchone()["name"]
    finally:
        conn.close()

    assert applied == migration_name


def test_run_migrations_rejects_existing_transaction(
    db_connection: sqlite3.Connection,
    tmp_path: Path,
):
    db_connection.execute("create table caller_data (value text)")
    db_connection.execute("insert into caller_data (value) values ('pending')")
    (tmp_path / "001_create.sql").write_text(
        "create table migration_data (id integer primary key)"
    )

    with pytest.raises(RuntimeError, match="active transaction"):
        run_migrations(db_connection, migrations_dir=str(tmp_path))

    assert db_connection.in_transaction is True
    db_connection.rollback()
    remaining = db_connection.execute("select * from caller_data").fetchall()
    assert remaining == []


@pytest.mark.parametrize(
    "transaction_statement",
    [
        "BEGIN",
        "COMMIT",
        "END",
        "ROLLBACK",
        "SAVEPOINT nested",
        "RELEASE nested",
        "-- leading comment\nCOMMIT",
        "/* leading comment */ ROLLBACK",
        "\ufeffBEGIN",
        "\ufeffCOMMIT",
        "\ufeffEND",
        "\ufeffROLLBACK",
        "\ufeffSAVEPOINT nested",
        "\ufeffRELEASE nested",
    ],
)
def test_run_migrations_rejects_transaction_control(
    tmp_path: Path,
    transaction_statement: str,
):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_escaped.sql").write_text(
        f"create table escaped_schema (id integer primary key);{transaction_statement};"
    )
    db_path = tmp_path / "escaped.db"

    conn = create_db(str(db_path))
    with pytest.raises(ValueError, match="transaction control"):
        run_migrations(conn, migrations_dir=str(migrations_dir))
    conn.close()

    reopened = create_db(str(db_path))
    try:
        tables = _table_names(reopened)
        applied = reopened.execute("select name from _migrations").fetchall()
    finally:
        reopened.close()

    assert "escaped_schema" not in tables
    assert applied == []


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
