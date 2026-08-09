"""用固定 read/write operation 观察业务 SQLite，不保存 SQL、参数或路径。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

from trowel_py.telemetry.events import emit_metric, emit_span
from trowel_py.telemetry.port import NoopTelemetryPort, TelemetryPort

SQLiteDomain = Literal["sessions", "workspaces"]

_port: TelemetryPort = NoopTelemetryPort()
_port_lock = threading.Lock()


def configure_sqlite_telemetry(port: TelemetryPort | None) -> None:
    """替换进程内业务 SQLite 使用的非阻塞遥测端口。

    Args:
        port: 当前应用遥测端口；None 恢复 no-op。
    """

    global _port
    with _port_lock:
        _port = port or NoopTelemetryPort()


def open_observed_sqlite(
    database: str | Path,
    *,
    domain: SQLiteDomain,
    timeout: float = 5.0,
    uri: bool = False,
) -> sqlite3.Connection:
    """创建只会上报固定领域 read/write 名称的 SQLite 连接。

    Args:
        database: 交给 sqlite3 的本地数据库路径或 URI，不进入遥测。
        domain: sessions 或 workspaces。
        timeout: SQLite 写锁等待秒数。
        uri: database 是否按 SQLite URI 解析。

    Returns:
        与普通 sqlite3.Connection 兼容的观察连接。
    """

    connection = sqlite3.connect(
        str(database),
        timeout=timeout,
        uri=uri,
        factory=ObservedSQLiteConnection,
    )
    connection.set_observation_domain(domain)
    return connection


class ObservedSQLiteConnection(sqlite3.Connection):
    """在 execute 边界记录稳定逻辑名，并原样传播 SQLite 结果或异常。"""

    def set_observation_domain(self, domain: SQLiteDomain) -> None:
        """设置当前连接所属的固定业务领域。

        Args:
            domain: sessions 或 workspaces。
        """

        self._observation_domain = domain

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        """观察一次单语句执行，不记录 SQL 和参数。"""

        return self._observe(
            sql,
            lambda: super(ObservedSQLiteConnection, self).execute(sql, parameters),
        )

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> sqlite3.Cursor:
        """观察一次批量语句执行，不记录 SQL 和参数集合。"""

        return self._observe(
            sql,
            lambda: super(ObservedSQLiteConnection, self).executemany(
                sql,
                seq_of_parameters,
            ),
        )

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        """把 schema 或迁移脚本统一视为领域写操作。"""

        return self._observe(
            "WRITE",
            lambda: super(ObservedSQLiteConnection, self).executescript(sql_script),
        )

    def _observe(
        self, sql: str, call: Callable[[], sqlite3.Cursor]
    ) -> sqlite3.Cursor:
        """计时调用并只按领域、读写和 SQLite 错误码提交事实。

        Args:
            sql: 只在内存中判断首个关键字的 SQL，不传给遥测。
            call: 实际执行 sqlite3 操作的零参数函数。

        Returns:
            sqlite3 原始 cursor。
        """

        domain = getattr(self, "_observation_domain", None)
        if domain not in {"sessions", "workspaces"}:
            return call()
        operation = f"sqlite.{domain}.{_access_kind(sql)}"
        started_at = datetime.now(UTC)
        try:
            cursor = call()
        except sqlite3.Error as exc:
            ended_at = datetime.now(UTC)
            category = _lock_category(exc)
            emit_span(
                _current_port(),
                component="sqlite",
                operation=operation,
                started_at=started_at,
                ended_at=ended_at,
                status="error",
                attributes={
                    "quality": "reliable",
                    "transport": "sqlite",
                    "error_category": category or "unknown",
                },
            )
            if category is not None:
                emit_metric(
                    _current_port(),
                    component="sqlite",
                    name=f"sqlite.{category}",
                    kind="counter",
                    unit="1",
                    value=1,
                    status="error",
                    operation=operation,
                    observed_at=ended_at,
                    attributes={
                        "quality": "reliable",
                        "transport": "sqlite",
                        "error_category": category,
                    },
                )
            raise
        emit_span(
            _current_port(),
            component="sqlite",
            operation=operation,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            attributes={"quality": "reliable", "transport": "sqlite"},
        )
        return cursor


def _current_port() -> TelemetryPort:
    """读取当前进程配置的 SQLite 遥测端口。"""

    with _port_lock:
        return _port


def _access_kind(sql: str) -> Literal["read", "write"]:
    """只看首个关键字，把语句压缩成 read 或 write。

    Args:
        sql: 仅在当前调用栈存在的 SQLite 语句。

    Returns:
        SELECT、WITH 和不带赋值的 PRAGMA 为 read；其他语句为 write。
    """

    normalized = sql.lstrip()
    keyword = normalized.split(maxsplit=1)[0].upper() if normalized else ""
    if keyword in {"SELECT", "WITH"}:
        return "read"
    if keyword == "PRAGMA":
        return "write" if "=" in normalized else "read"
    return "write"


def _lock_category(exc: sqlite3.Error) -> Literal["busy", "locked"] | None:
    """用 SQLite 主结果码区分 BUSY 和 LOCKED。

    Args:
        exc: sqlite3 保留原生 sqlite_errorcode 的异常。

    Returns:
        主结果码 5 为 busy、6 为 locked，其他错误为 None。
    """

    code = getattr(exc, "sqlite_errorcode", None)
    primary = code & 0xFF if isinstance(code, int) else None
    if primary == sqlite3.SQLITE_BUSY:
        return "busy"
    if primary == sqlite3.SQLITE_LOCKED:
        return "locked"
    return None
