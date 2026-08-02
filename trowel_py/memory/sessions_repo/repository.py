"""组合 Claude、Codex 与关闭请求三个有明确边界的仓储。"""

from __future__ import annotations

import sqlite3

from .claude import ClaudeSessionsRepository
from .codex import CodexTurnsRepository
from .database import initialize_schema
from .review_requests import ReviewRequestsRepository


class SessionsRepository:
    """共享一个 SQLite 连接的 sessions registry composition root。

    该对象本身不提供 runtime 业务方法。调用方必须显式选择 ``claude``、
    ``codex`` 或 ``review_requests``，让读取代码时能直接看出数据归属和副作用。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        migrate: bool = True,
    ) -> None:
        """初始化 schema，并创建三个共享连接的作用域仓储。"""
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        if migrate:
            initialize_schema(self._conn)
            self._conn.commit()
        self.claude = ClaudeSessionsRepository(self._conn)
        self.codex = CodexTurnsRepository(self._conn)
        self.review_requests = ReviewRequestsRepository(self._conn)


def create_sessions_repository(
    conn: sqlite3.Connection,
    *,
    migrate: bool = True,
) -> SessionsRepository:
    """用调用方连接创建 sessions registry composition root。"""
    return SessionsRepository(conn, migrate=migrate)
