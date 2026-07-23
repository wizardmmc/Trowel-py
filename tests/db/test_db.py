import sqlite3
from pathlib import Path
import tempfile
import pytest

from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations

def test_create_db_sets_params():
    """
    verify WAL mode and foreign key are enabled
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = create_db(db_path)
        # PRAGMA - a command to look up or change the sql setting, return like {"journal_mode": "wal"}
        # fetchone - retrieve next row at once
        wal = conn.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()["foreign_keys"]
        assert wal == "wal"
        assert fk == 1
    finally:
        conn.close()
        Path(db_path).unlink()

def test_create_db_returns_connections():
    """
    verify create_db return a valid SQLite connection
    """
    conn = create_db(":memory:")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()

def test_run_migrations_creates_table(db_connection: sqlite3.Connection):
    """
    verify run_migrations creates the _migrations table
    """
    run_migrations(db_connection, str(Path(__file__).parent / "empty_migrations"))
    # fetchall - retrieve all remaining content at once
    tables = db_connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert any(row["name"] == '_migrations' for row in tables)

def test_migration_idempotent(db_connection: sqlite3.Connection):
    """
    verify idempotency
    """
    with tempfile.TemporaryDirectory() as tmp:  # TemporaryDirectory is cleaned up when the context exits
        sql_file = Path(tmp) / "001_test.sql"
        sql_file.write_text("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
        run_migrations(db_connection, migrations_dir=tmp)
        run_migrations(db_connection, migrations_dir=tmp)
        count = db_connection.execute("SELECT COUNT(*) as c FROM _migrations").fetchone()["c"]
        assert count == 1

def test_001_migration(db_connection: sqlite3.Connection):
    """
    verify whether can run 001_create_cards.sql success
    """
    run_migrations(db_connection)
    # check cards and cards_fts exists and cards's field
    tables = db_connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert any('cards' == row["name"] for row in tables)
    assert any('cards_fts' == row["name"] for row in tables)
    column_names = [row["name"] for row in db_connection.execute("PRAGMA table_info('cards')").fetchall()]
    assert column_names == ['id', 'title', 'category', 'explanation', 'example', 'difficulty', 'source', 'tags', 'status', 'created_at', 'updated_at']
    # full-text search check
    db_connection.execute(
        "INSERT INTO cards (id, title, category, explanation, tags) VALUES (?, ?, ?, ?, ?)",
        ('test-1', 'Test Python Card', 'python', 'A card for testing 001 migration', '["python", "test"]')
    )
    result =  db_connection.execute("SELECT * FROM cards_fts WHERE cards_fts MATCH 'python'").fetchall()
    assert len(result) == 1

def test_002_003_migration(db_connection: sqlite3.Connection):
    """
    verify migrations 002 and 003 create all tables and foreign key cascade works
    """
    run_migrations(db_connection)
    # check tables are created correctly
    tables = db_connection.execute("select name from sqlite_master where type='table'").fetchall()
    table_names = {row["name"] for row in tables}
    expected_tables = ['fsrs_state', 'review_logs', 'card_explanation_history', 'players', 'pets',
                       'inventory', 'event_log', 'event_cooldowns', 'user_preferences', 'cold_start_answers']
    assert all(t in table_names for t in expected_tables)
    # test foreign key
    db_connection.execute(
        "insert into cards (id, title, category, explanation, tags) values (?, ?, ?, ?, ?)",
        ('test-2', 'test fsrs_state foregin-key', 'java', "a card for testing 002 fsrs_state's foreign key", '["java", "test"]')
    )
    db_connection.execute(
        "insert into fsrs_state (card_id, state) values (?, ?)",
        ('test-2', 2)
    )
    db_connection.execute(
        "delete from cards where id == 'test-2'"
    )
    result = db_connection.execute("select * from fsrs_state").fetchall()
    assert len(result) == 0


def _insert_card(conn, card_id="test-card"):
    """insert a parent card so FK-referencing rows can be created."""
    conn.execute(
        "insert into cards (id, title, category, explanation, tags) values (?, ?, ?, ?, ?)",
        (card_id, "test card", "python", "for FK tests", '["python"]'),
    )


def test_005_migration(db_connection: sqlite3.Connection):
    """
    verify migration 005 creates feynman/follow-up tables with correct fields,
    FK enforcement, cascading deletes, and CHECK constraints.
    """
    run_migrations(db_connection)

    # --- 1. tables exist with the right columns ---
    tables = {row["name"] for row in db_connection.execute(
        "select name from sqlite_master where type='table'"
    ).fetchall()}
    for t in ("feynman_sessions", "follow_up_threads", "follow_up_messages"):
        assert t in tables, f"missing table {t}"

    feynman_cols = {row["name"] for row in db_connection.execute(
        "pragma table_info('feynman_sessions')"
    ).fetchall()}
    assert {"id", "card_id", "question", "accuracy", "completeness",
            "feedback", "missed_points"}.issubset(feynman_cols)

    # --- 2a. FK cascade: deleting a card drops its feynman_session ---
    _insert_card(db_connection)
    db_connection.execute(
        "insert into feynman_sessions (id, card_id, question, user_answer) "
        "values (?, ?, ?, ?)",
        ("sess-1", "test-card", "what is a closure?", "a nested function"),
    )
    db_connection.execute("delete from cards where id = 'test-card'")
    remaining = db_connection.execute(
        "select count(*) as c from feynman_sessions"
    ).fetchone()["c"]
    assert remaining == 0, "feynman_sessions should cascade-delete with its card"

    # --- 2b. FK cascade (two-level): thread -> messages, card -> threads ---
    _insert_card(db_connection)
    db_connection.execute(
        "insert into follow_up_threads (id, card_id) values (?, ?)",
        ("thread-1", "test-card"),
    )
    db_connection.execute(
        "insert into follow_up_messages (id, thread_id, role, content) "
        "values (?, ?, ?, ?)",
        ("msg-1", "thread-1", "user", "why does the closure survive?"),
    )
    db_connection.execute("delete from follow_up_threads where id = 'thread-1'")
    remaining_msgs = db_connection.execute(
        "select count(*) as c from follow_up_messages"
    ).fetchone()["c"]
    assert remaining_msgs == 0, "follow_up_messages should cascade-delete with its thread"

    # --- 2c. FK enforcement: orphan card_id must be rejected ---
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            "insert into follow_up_threads (id, card_id) values (?, ?)",
            ("thread-orphan", "no-such-card"),
        )

    # --- 3. CHECK constraints reject out-of-range values ---
    _insert_card(db_connection, card_id="card-check")
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            "insert into feynman_sessions (id, card_id, question, accuracy) "
            "values (?, ?, ?, ?)",
            ("sess-acc", "card-check", "q", 150),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            "insert into feynman_sessions (id, card_id, question, completeness) "
            "values (?, ?, ?, ?)",
            ("sess-comp", "card-check", "q", -1),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            "insert into follow_up_messages (id, thread_id, role, content) "
            "values (?, ?, ?, ?)",
            ("msg-role", "thread-1", "system", "invalid role"),
        )

    # --- 4. nullable accuracy passes (NULL is allowed by the CHECK) ---
    db_connection.execute(
        "insert into feynman_sessions (id, card_id, question) values (?, ?, ?)",
        ("sess-null", "card-check", "q"),
    )
    row = db_connection.execute(
        "select accuracy from feynman_sessions where id = 'sess-null'"
    ).fetchone()
    assert row["accuracy"] is None, "accuracy must allow NULL (degradation case)"

