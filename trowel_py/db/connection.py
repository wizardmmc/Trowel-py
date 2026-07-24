import sqlite3


def create_db(db_path: str = "trowel.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL 允许读取与写入并行；外键检查是 SQLite 的连接级开关。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
