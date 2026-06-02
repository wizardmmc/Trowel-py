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
