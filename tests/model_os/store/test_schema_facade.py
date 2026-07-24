from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trowel_py.model_os import store as store_module
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.store_schema import SCHEMA_SQL


def test_store_schema_constant_is_module_alias() -> None:
    from trowel_py.model_os import store_schema

    assert store_module._SCHEMA_SQL is store_schema.SCHEMA_SQL


def test_bootstrap_reads_store_schema_global_at_call_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_schema = """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE foreground_claim (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        task_id TEXT
    );
    CREATE TABLE runtime_schema_probe (value TEXT);
    """
    monkeypatch.setattr(store_module, "_SCHEMA_SQL", runtime_schema)
    monkeypatch.setattr(ModelOsStore, "_migrate_schema", lambda self: None)

    store = ModelOsStore(tmp_path / "runtime-schema.db")
    store.open()
    try:
        row = store._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='runtime_schema_probe'"
        ).fetchone()
        assert row["name"] == "runtime_schema_probe"
    finally:
        store.close()


def test_schema_constraints_preserve_store_invariants() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQL)

    connection.execute("INSERT INTO foreground_claim (id, task_id) VALUES (1, NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO foreground_claim (id, task_id) VALUES (2, NULL)"
        )

    for table, entity_column in (
        ("task_create_keys", "task_id"),
        ("episode_create_keys", "episode_id"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO {table} "
                f"(idempotency_key, {entity_column}, created_at) VALUES (?, ?, ?)",
                (" ", "entity-example", "2026-01-01T00:00:00+00:00"),
            )

    lease_values = (
        "lease-a",
        "episode",
        "episode-example",
        "owner-a",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T01:00:00+00:00",
        "key-example",
    )
    connection.execute(
        "INSERT INTO leases "
        "(lease_id, resource_type, resource_id, owner, acquired_at, expires_at, "
        "idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
        lease_values,
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO leases "
            "(lease_id, resource_type, resource_id, owner, acquired_at, expires_at, "
            "idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("lease-b", *lease_values[1:6], "other-key"),
        )

    connection.execute(
        "UPDATE leases SET released_at=? WHERE lease_id=?",
        ("2026-01-01T00:30:00+00:00", "lease-a"),
    )
    connection.execute(
        "INSERT INTO leases "
        "(lease_id, resource_type, resource_id, owner, acquired_at, expires_at, "
        "idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("lease-c", *lease_values[1:]),
    )
