import sqlite3
import threading
from pathlib import Path

import trowel_py.db.connection as connection_module
from trowel_py.db.connection import _requires_serialized_open_close, create_db


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


def test_only_known_unix_sqlite_deadlock_versions_enable_compatibility_lock() -> None:
    """兼容锁不能延伸到已修复版本或不受影响的平台。"""

    assert _requires_serialized_open_close((3, 51, 0), "darwin") is True
    assert _requires_serialized_open_close((3, 51, 1), "linux") is True
    assert _requires_serialized_open_close((3, 50, 4), "darwin") is False
    assert _requires_serialized_open_close((3, 51, 2), "darwin") is False
    assert _requires_serialized_open_close((3, 51, 1), "win32") is False


def test_known_sqlite_deadlock_versions_serialize_connection_open_and_close(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """已知缺陷版本必须阻止 SQLite 在不同线程同时打开和关闭主库。"""

    class TrackingLock:
        """记录数据库连接生命周期实际进入兼容锁的次数。"""

        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.enter_count = 0

        def __enter__(self) -> None:
            self._lock.acquire()
            self.enter_count += 1

        def __exit__(self, exc_type, exc, traceback) -> None:
            self._lock.release()

    lock = TrackingLock()
    monkeypatch.setattr(
        connection_module,
        "_SERIALIZE_SQLITE_OPEN_CLOSE",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        connection_module,
        "_SQLITE_OPEN_CLOSE_LOCK",
        lock,
        raising=False,
    )

    connection = create_db(tmp_path / "deadlock-regression.db")
    connection.close()

    assert lock.enter_count == 2
