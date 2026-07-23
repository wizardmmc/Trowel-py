import sqlite3
from pathlib import Path

from trowel_py.db.connection import create_db


def test_create_db_enables_wal_and_foreign_keys(tmp_path: Path):
    conn = create_db(str(tmp_path / "settings.db"))
    try:
        journal_mode = conn.execute("pragma journal_mode").fetchone()["journal_mode"]
        foreign_keys = conn.execute("pragma foreign_keys").fetchone()["foreign_keys"]
    finally:
        conn.close()

    assert journal_mode == "wal"
    assert foreign_keys == 1


def test_create_db_returns_connection():
    conn = create_db(":memory:")
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()
