"""验证业务 SQLite 只上报固定逻辑名，并按原生结果码区分 busy。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trowel_py.telemetry.sqlite import (
    configure_sqlite_telemetry,
    open_observed_sqlite,
)


class RecordingPort:
    """记录 SQLite 测试发出的 span 与 metric。"""

    def __init__(self) -> None:
        """创建空事实列表。"""

        self.spans = []
        self.metrics = []

    def emit_span(self, span):
        """保存一条 span。"""

        self.spans.append(span)
        return None

    def emit_metric(self, metric):
        """保存一条 metric。"""

        self.metrics.append(metric)
        return None


def test_observed_sqlite_reports_busy_without_sql_or_path(tmp_path: Path) -> None:
    """真实写锁超时产生 busy；遥测中只有 sessions.write 固定名。"""

    port = RecordingPort()
    configure_sqlite_telemetry(port)
    path = tmp_path / "private-sessions.db"
    first = open_observed_sqlite(path, domain="sessions", timeout=0.01)
    second = open_observed_sqlite(path, domain="sessions", timeout=0.01)
    try:
        first.execute("CREATE TABLE facts(value TEXT)")
        first.commit()
        first.execute("BEGIN IMMEDIATE")
        first.execute("INSERT INTO facts(value) VALUES ('secret-value')")

        with pytest.raises(sqlite3.OperationalError):
            second.execute("INSERT INTO facts(value) VALUES ('other-secret')")
    finally:
        first.rollback()
        first.close()
        second.close()
        configure_sqlite_telemetry(None)

    assert port.metrics[-1].name == "sqlite.busy"
    assert port.metrics[-1].operation == "sqlite.sessions.write"
    encoded = "".join(item.model_dump_json() for item in [*port.spans, *port.metrics])
    assert "secret-value" not in encoded
    assert str(path) not in encoded


def test_observed_sqlite_reports_locked_from_shared_cache(tmp_path: Path) -> None:
    """同进程共享缓存的表级冲突按 SQLITE_LOCKED 主结果码统计。"""

    port = RecordingPort()
    configure_sqlite_telemetry(port)
    uri = f"file:{tmp_path / 'locked.db'}?cache=shared"
    first = open_observed_sqlite(uri, domain="workspaces", timeout=0.01, uri=True)
    second = open_observed_sqlite(uri, domain="workspaces", timeout=0.01, uri=True)
    try:
        first.execute("CREATE TABLE facts(value TEXT)")
        first.execute("INSERT INTO facts(value) VALUES ('value')")
        first.commit()
        first.execute("BEGIN")
        cursor = first.execute("SELECT value FROM facts")

        with pytest.raises(sqlite3.OperationalError):
            second.execute("DROP TABLE facts")
        cursor.close()
    finally:
        first.rollback()
        first.close()
        second.close()
        configure_sqlite_telemetry(None)

    assert port.metrics[-1].name == "sqlite.locked"
    assert port.metrics[-1].operation == "sqlite.workspaces.write"


def test_mutating_pragma_is_classified_as_write(tmp_path: Path) -> None:
    """带赋值的 PRAGMA 改变连接或数据库状态，不能混进读取分布。"""

    port = RecordingPort()
    configure_sqlite_telemetry(port)
    connection = open_observed_sqlite(tmp_path / "pragma.db", domain="sessions")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    finally:
        connection.close()
        configure_sqlite_telemetry(None)

    assert port.spans[-1].operation == "sqlite.sessions.write"
