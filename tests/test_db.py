import sqlite3
from pathlib import Path
import tempfile

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

