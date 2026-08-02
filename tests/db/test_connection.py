import sqlite3
from pathlib import Path

from trowel_py.db.connection import create_db


def test_default_database_path_uses_application_root_without_chdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """桌面数据覆盖必须直接决定主数据库位置，不能依赖进程工作目录。"""

    workdir = tmp_path / "workdir"
    data_root = tmp_path / "application-data"
    workdir.mkdir()
    data_root.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("TROWEL_DATA_ROOT", str(data_root))

    connection = create_db()
    connection.close()

    assert (data_root / "trowel.db").exists()
    assert not (workdir / "trowel.db").exists()


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
