"""用 SQLite 稳定保存 Agent 最近打开的工作区。"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trowel_py.application_paths import resolve_application_data_root
from trowel_py.telemetry.sqlite import open_observed_sqlite


class WorkspaceUnavailableError(ValueError):
    """表示用户选择的工作目录不存在或不是目录。"""


@dataclass(frozen=True)
class RecentWorkspace:
    """描述一个曾由用户打开的 Agent 工作区。

    Attributes:
        path: 规范化后的绝对目录路径，用于创建会话和去重。
        name: 目录最后一段名称，用于 Recent 列表展示。
        last_opened_at: 最近一次确认打开该目录的 UTC 时间。
        available: 当前读取时该路径是否仍是可访问目录。
    """

    path: str
    name: str
    last_opened_at: datetime
    available: bool

    def to_dict(self) -> dict[str, object]:
        """转换成 Agent HTTP 接口返回的字段。"""

        return {
            "path": self.path,
            "name": self.name,
            "last_opened_at": self.last_opened_at.isoformat(),
            "available": self.available,
        }


def resolve_recent_workspaces_path() -> Path:
    """返回 Recent 工作区数据库路径。

    ``TROWEL_WORKSPACES_PATH`` 非空时使用该路径，便于隔离测试和桌面数据目录；
    否则使用所有本地 renderer 共用的 ``~/.trowel/workspaces.db``。
    """

    override = os.environ.get("TROWEL_WORKSPACES_PATH", "").strip()
    return (
        Path(override).expanduser()
        if override
        else resolve_application_data_root() / "workspaces.db"
    )


class RecentWorkspaceStore:
    """按最近打开顺序保存有限数量的工作区。

    SQLite 负责跨线程和跨进程串行化，Web 与 Desktop 即使经过不同 renderer
    访问，也读取同一份路径事实。

    Attributes:
        path: 保存 Recent 工作区的 SQLite 数据库路径。
        limit: 最多保留的去重路径数量。
    """

    def __init__(
        self,
        path: Path,
        *,
        limit: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建 Recent 工作区仓储。

        Args:
            path: SQLite 数据库路径；父目录会在首次操作时创建。
            limit: 去重后最多保存多少个路径，必须大于零。
            clock: 记录最近打开时间的 UTC 时钟；测试可传确定性时钟。

        Raises:
            ValueError: ``limit`` 小于一。
        """

        if limit < 1:
            raise ValueError("recent workspace limit must be positive")
        self._path = path
        self._limit = limit
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    @property
    def path(self) -> Path:
        """返回保存 Recent 工作区的数据库路径。"""

        return self._path

    @property
    def limit(self) -> int:
        """返回去重后的最大 Recent 数量。"""

        return self._limit

    def remember(self, raw_path: str) -> RecentWorkspace:
        """校验并把工作区移动到 Recent 首位。

        Args:
            raw_path: 用户从 Recent、原生选择器或 Web 浏览器选中的目录。

        Returns:
            已规范化并持久化的工作区记录。

        Raises:
            WorkspaceUnavailableError: 路径不存在或不是目录。
        """

        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            raise WorkspaceUnavailableError(f"workspace does not exist: {path}")
        opened_at = self._clock().astimezone(UTC)
        with self._connect() as connection:
            connection.execute("DELETE FROM recent_workspaces WHERE path = ?", (str(path),))
            connection.execute(
                "INSERT INTO recent_workspaces(path, opened_at) VALUES (?, ?)",
                (str(path), opened_at.isoformat()),
            )
            connection.execute(
                """
                DELETE FROM recent_workspaces
                WHERE id NOT IN (
                    SELECT id FROM recent_workspaces ORDER BY id DESC LIMIT ?
                )
                """,
                (self._limit,),
            )
        return self._record(str(path), opened_at)

    def list_recent(self) -> list[RecentWorkspace]:
        """按最近打开顺序返回工作区，并现场检查路径可用性。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, opened_at FROM recent_workspaces ORDER BY id DESC"
            ).fetchall()
        return [
            self._record(
                str(row["path"]),
                datetime.fromisoformat(str(row["opened_at"])),
            )
            for row in rows
        ]

    def _record(self, path: str, opened_at: datetime) -> RecentWorkspace:
        """根据持久化字段生成包含当前可用性的工作区记录。"""

        target = Path(path)
        return RecentWorkspace(
            path=path,
            name=target.name or path,
            last_opened_at=opened_at,
            available=target.is_dir(),
        )

    def _connect(self) -> sqlite3.Connection:
        """打开数据库并确保 Recent 表可用。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = open_observed_sqlite(
            self._path,
            domain="workspaces",
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS recent_workspaces(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                opened_at TEXT NOT NULL
            )
            """
        )
        return connection
