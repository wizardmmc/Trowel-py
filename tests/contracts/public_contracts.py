from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from trowel_py.app import create_app
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations
from trowel_py.memory.sessions_repo import SessionsRepository
from trowel_py.model_os.store import ModelOsStore
from trowel_py.schemas.agent_host import AGENT_EVENT_TYPES
from trowel_py.schemas.cc_host import EVENT_TYPES

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "public-contracts.json"

_CLI_COMMANDS = (
    (),
    ("memory",),
    ("memory", "tidy"),
    ("memory", "review"),
    ("memory", "repair"),
    ("memory", "backfill-completed"),
    ("memory", "dict-rebuild"),
    ("memory", "dict-check"),
    ("memory", "migrate"),
    ("memory", "core"),
    ("memory", "core", "nominate"),
    ("memory", "core", "approve"),
    ("memory", "core", "activate"),
    ("memory", "metrics"),
    ("memory", "promotion"),
    ("memory", "profile-recalibrate"),
)

_FTS_SHADOW_TABLES = {
    "cards_fts_config",
    "cards_fts_content",
    "cards_fts_data",
    "cards_fts_docsize",
    "cards_fts_idx",
}


def _cli_help(parts: tuple[str, ...]) -> str:
    env = os.environ.copy()
    env.update({"COLUMNS": "80", "LINES": "24"})
    result = subprocess.run(
        [sys.executable, "-m", "trowel_py.cli", *parts, "--help"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout


def _without_sql_comments(sql: str) -> str:
    result: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            result.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"', "`"}:
            quote = char
            result.append(char)
        elif char == "[":
            quote = "]"
            result.append(char)
        elif char == "-" and index + 1 < len(sql) and sql[index + 1] == "-":
            index += 2
            while index < len(sql) and sql[index] != "\n":
                index += 1
            result.append(" ")
            continue
        elif char == "/" and index + 1 < len(sql) and sql[index + 1] == "*":
            index += 2
            while index + 1 < len(sql) and sql[index : index + 2] != "*/":
                index += 1
            index = min(index + 2, len(sql))
            result.append(" ")
            continue
        else:
            result.append(char)
        index += 1
    return " ".join("".join(result).split())


def _schema_objects(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql "
        "FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY type, name"
    ).fetchall()
    return [
        {
            "type": str(row[0]),
            "name": str(row[1]),
            "table": str(row[2]),
            "sql": _without_sql_comments(str(row[3])),
        }
        for row in rows
        if not str(row[1]).startswith("sqlite_")
        and str(row[1]) not in _FTS_SHADOW_TABLES
    ]


def _database_schemas() -> dict[str, list[dict[str, str]]]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        main_path = root / "trowel.db"
        main_conn = create_db(str(main_path))
        run_migrations(main_conn)
        main_schema = _schema_objects(main_conn)
        main_conn.close()

        sessions_conn = sqlite3.connect(root / "sessions.db")
        SessionsRepository(sessions_conn)
        sessions_schema = _schema_objects(sessions_conn)
        sessions_conn.close()

        model_os_path = root / "model-os.db"
        model_os_store = ModelOsStore(model_os_path)
        model_os_store.open()
        model_os_store.close()
        model_os_conn = sqlite3.connect(model_os_path)
        model_os_schema = _schema_objects(model_os_conn)
        model_os_conn.close()

    return {
        "main": main_schema,
        "memory_sessions": sessions_schema,
        "model_os": model_os_schema,
    }


def capture_public_contracts() -> dict[str, Any]:
    cli_help = {
        " ".join(parts) if parts else "root": _cli_help(parts)
        for parts in _CLI_COMMANDS
    }
    return {
        "cli_help": cli_help,
        "database_schemas": _database_schemas(),
        "event_types": {
            "agent": sorted(AGENT_EVENT_TYPES),
            "cc": sorted(EVENT_TYPES),
        },
        "openapi": create_app().openapi(),
    }


def main() -> None:
    if sys.argv[1:] != ["--update"]:
        raise SystemExit(
            "usage: python -m tests.contracts.public_contracts --update"
        )
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(capture_public_contracts(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"updated {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
