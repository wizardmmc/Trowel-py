import sqlite3
from pathlib import Path

_TRANSACTION_CONTROL_STATEMENTS = {
    "BEGIN",
    "COMMIT",
    "END",
    "RELEASE",
    "ROLLBACK",
    "SAVEPOINT",
}


def _pending_migration_files(
    conn: sqlite3.Connection,
    migrations_dir: Path,
) -> list[Path]:
    executed = {
        row["name"] for row in conn.execute("SELECT name from _migrations").fetchall()
    }
    return [
        migration
        for migration in sorted(migrations_dir.glob("*.sql"))
        if migration.name not in executed
    ]


def _sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    start = 0
    for end, character in enumerate(script):
        if character != ";":
            continue
        candidate = script[start : end + 1]
        if sqlite3.complete_statement(candidate):
            if candidate.strip():
                statements.append(candidate)
            start = end + 1
    trailing = script[start:]
    if trailing.strip():
        statements.append(trailing)
    return statements


def _strip_sql_leading_whitespace(text: str) -> str:
    start = 0
    while start < len(text):
        character = text[start]
        if not character.isspace() and character != "\ufeff":
            break
        start += 1
    return text[start:]


def _first_sql_keyword(statement: str) -> str | None:
    remaining = _strip_sql_leading_whitespace(statement)
    while remaining:
        if remaining.startswith("--"):
            _, separator, remaining = remaining.partition("\n")
            if not separator:
                return None
            remaining = _strip_sql_leading_whitespace(remaining)
            continue
        if remaining.startswith("/*"):
            comment_end = remaining.find("*/", 2)
            if comment_end == -1:
                return None
            remaining = _strip_sql_leading_whitespace(remaining[comment_end + 2 :])
            continue
        break

    keyword_end = 0
    while keyword_end < len(remaining) and remaining[keyword_end].isalpha():
        keyword_end += 1
    if keyword_end == 0:
        return None
    return remaining[:keyword_end].upper()


def _validate_migration_script(migration: Path, script: str) -> None:
    for statement in _sql_statements(script):
        keyword = _first_sql_keyword(statement)
        if keyword in _TRANSACTION_CONTROL_STATEMENTS:
            raise ValueError(
                f"migration {migration.name!r} contains transaction control "
                f"statement {keyword}"
            )


def _run_migration(conn: sqlite3.Connection, migration: Path) -> None:
    migration_script = migration.read_text()
    _validate_migration_script(migration, migration_script)
    name_literal = conn.execute("SELECT quote(?)", (migration.name,)).fetchone()[0]
    script = (
        "BEGIN;\n"
        f"{migration_script}\n"
        ";\n"
        "INSERT INTO _migrations (name) "
        f"VALUES ({name_literal});\n"
        "COMMIT;"
    )
    try:
        conn.executescript(script)
    except BaseException:
        conn.rollback()
        raise


def run_migrations(conn: sqlite3.Connection, migrations_dir: str | None = None) -> None:
    """按文件名顺序原子执行迁移；连接和脚本均不得自行持有事务。"""
    if conn.in_transaction:
        raise RuntimeError("run_migrations does not accept an active transaction")

    if migrations_dir is None:
        migrations_dir = str(Path(__file__).parent / "migrations")
    directory = Path(migrations_dir)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations(
            name TEXT PRIMARY KEY
        )"""
    )

    for migration in _pending_migration_files(conn, directory):
        _run_migration(conn, migration)
