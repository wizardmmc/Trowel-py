from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import DecisionDisposition


V4_DECISIONS_SQL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES ('schema_version', '4');
CREATE TABLE decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    work_item_id TEXT,
    task_id TEXT,
    episode_id TEXT,
    cause_id TEXT,
    correlation_id TEXT,
    policy_version TEXT NOT NULL,
    signals TEXT NOT NULL,
    candidates TEXT NOT NULL,
    choice TEXT NOT NULL,
    reason TEXT NOT NULL,
    budget_before TEXT,
    budget_after TEXT
);
INSERT INTO decisions (
    decision_id, kind, decided_at, policy_version, signals, candidates,
    choice, reason, budget_before, budget_after
) VALUES (
    'decision.legacy', 'route', '2026-07-20T00:00:00Z', 'v0',
    '{"prompt":"private legacy body"}', '["fast","deep"]',
    'deep', 'free text legacy reason', NULL, NULL
);
"""


def _create_v4(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(V4_DECISIONS_SQL)
    conn.close()


def test_v4_decision_migrates_without_guessing_or_changing_rows(tmp_path: Path) -> None:
    path = tmp_path / "v4.db"
    _create_v4(path)

    store = ModelOsStore(path)
    store.open()
    try:
        [(seq, decision)] = store.list_decisions()
        assert seq == 1
        assert decision.decision_id == "decision.legacy"
        assert decision.reason == "free text legacy reason"
        assert decision.signals == {"prompt": "private legacy body"}
        assert decision.disposition == DecisionDisposition.LEGACY_UNKNOWN
        assert store._schema_version() == 6
        assert store._conn is not None
        row = store._conn.execute(
            "SELECT identity_hash FROM decisions WHERE seq=1"
        ).fetchone()
        assert row["identity_hash"].startswith("sha256:")
    finally:
        store.close()

    reopened = ModelOsStore(path)
    reopened.open()
    try:
        assert reopened._schema_version() == 6
        assert len(reopened.list_decisions()) == 1
    finally:
        reopened.close()


def test_failed_v4_migration_does_not_advance_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "broken-v4.db"
    _create_v4(path)

    class FailingStore(ModelOsStore):
        def _migrate_v4_to_v5(self) -> None:
            assert self._conn is not None
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN disposition TEXT "
                "DEFAULT 'legacy_unknown'"
            )
            raise RuntimeError("injected migration failure")

    with pytest.raises(RuntimeError, match="injected migration failure"):
        FailingStore(path).open()

    conn = sqlite3.connect(path)
    try:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "4"
    finally:
        conn.close()


def test_failed_v5_to_v6_migration_does_not_advance_schema_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broken-v5.db"
    _create_v4(path)
    prepared = ModelOsStore(path)
    prepared.open()
    assert prepared._conn is not None
    prepared._conn.execute(
        "UPDATE meta SET value='5' WHERE key='schema_version'"
    )
    prepared._conn.execute("DROP TABLE projection_checkpoints")
    prepared._conn.commit()
    prepared.close()

    class FailingStore(ModelOsStore):
        def _migrate_v5_to_v6(self) -> None:
            assert self._conn is not None
            self._conn.execute(
                "CREATE TABLE migration_partial (value TEXT)"
            )
            raise RuntimeError("injected v6 migration failure")

    with pytest.raises(RuntimeError, match="injected v6 migration failure"):
        FailingStore(path).open()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0] == "5"
    finally:
        conn.close()
