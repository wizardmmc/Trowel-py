import sqlite3

def create_db(db_path: str = "trowel.db") -> sqlite3.Connection:
    """
    create a database connection instance
    """
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # allow access by database column names
    # By default, SQlite blocks the database during write operations. This command allows write operations to be queued in the log file first,
    # ensuring that reads are not blocked by writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")  # enable external link checking settings
    return conn

