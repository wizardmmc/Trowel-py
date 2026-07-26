from __future__ import annotations

import hashlib
import sqlite3

from trowel_py.model_os.work_broker.schema import SCHEMA_SQL

EXPECTED_COLUMNS = {
    "work_leases": (
        "lease_id",
        "slot",
        "provider",
        "account_id",
        "work_kind",
        "model_tier",
        "task_id",
        "work_item_id",
        "granted_cap",
        "started",
        "in_critical",
        "acquired_at",
        "expires_at",
        "fencing_token",
        "idempotency_key",
        "policy_version",
        "released_at",
    ),
    "work_fence_counters": ("slot", "last_token"),
    "work_idempotency_keys": (
        "idempotency_key",
        "lease_id",
        "fingerprint",
        "created_at",
    ),
    "work_usage": (
        "seq",
        "observation_id",
        "lease_id",
        "provider",
        "account_id",
        "work_kind",
        "model_tier",
        "task_id",
        "work_item_id",
        "calls",
        "input_tokens",
        "output_tokens",
        "cost",
        "wall_seconds",
        "occurred_at",
        "day",
        "policy_version",
    ),
    "work_catchup_watermark": (
        "scope",
        "period",
        "work_kind",
        "lease_id",
        "state",
        "claimed_at",
        "completed_at",
    ),
    "work_cleanup_commands": (
        "command_id",
        "work_kind",
        "before_at",
        "recovered_count",
        "occurred_at",
    ),
}

EXPECTED_INDEX_COLUMNS = {
    "idx_work_leases_active": ("slot",),
    "idx_work_usage_obs": ("lease_id", "observation_id"),
    "idx_work_usage_dim": (
        "work_kind",
        "provider",
        "account_id",
        "day",
    ),
}


def test_schema_bytes_are_stable() -> None:
    assert len(SCHEMA_SQL.encode()) == 4041
    assert hashlib.sha256(SCHEMA_SQL.encode()).hexdigest() == (
        "56324da4ca0084ec6d781df8b940c8f3cf0fa86bc79b44ffe52d812f8e2d980f"
    )


def test_schema_is_idempotent_and_has_expected_shape() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQL)
    connection.executescript(SCHEMA_SQL)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == set(EXPECTED_COLUMNS)

    for table, columns in EXPECTED_COLUMNS.items():
        actual = tuple(
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        )
        assert actual == columns

    for index, columns in EXPECTED_INDEX_COLUMNS.items():
        actual = tuple(
            row[2] for row in connection.execute(f"PRAGMA index_info({index})")
        )
        assert actual == columns
