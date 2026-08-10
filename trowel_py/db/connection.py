"""创建采用项目统一设置的 SQLite 连接。"""

import sqlite3
import stat
import sys
import threading
from pathlib import Path

from trowel_py.application_paths import (
    has_application_data_root_override,
    resolve_application_data_root,
)

_SQLITE_OPEN_CLOSE_LOCK = threading.Lock()


def _requires_serialized_open_close(
    sqlite_version: tuple[int, ...],
    platform: str,
) -> bool:
    """判断当前 SQLite 是否命中 Unix 打开/关闭连接死锁缺陷。"""

    return platform != "win32" and (3, 51, 0) <= sqlite_version < (3, 51, 2)


_SERIALIZE_SQLITE_OPEN_CLOSE = _requires_serialized_open_close(
    sqlite3.sqlite_version_info,
    sys.platform,
)


class _TrowelConnection(sqlite3.Connection):
    """在已知缺陷 SQLite 上把连接关闭纳入进程内生命周期锁。"""

    def close(self) -> None:
        """关闭连接，并避免与另一线程的数据库打开过程形成锁反转。"""

        if not _SERIALIZE_SQLITE_OPEN_CLOSE:
            super().close()
            return
        with _SQLITE_OPEN_CLOSE_LOCK:
            super().close()


def _open_connection(resolved_path: str | Path) -> _TrowelConnection:
    """使用项目统一参数打开一条可受控关闭的 SQLite 连接。"""

    return sqlite3.connect(
        resolved_path,
        timeout=10,
        check_same_thread=False,
        factory=_TrowelConnection,
    )


def resolve_database_path(db_path: str | Path | None = None) -> str | Path:
    """解析主数据库路径，同时保留显式路径与浏览器开发布局。

    Args:
        db_path: 调用方明确指定的文件或 SQLite 特殊路径；省略时由运行环境决定。

    Returns:
        桌面数据根存在时返回其中的 ``trowel.db``；普通 browser/CLI 环境继续返回
        当前工作目录下的相对路径。
    """

    if db_path is not None:
        return db_path
    if has_application_data_root_override():
        return resolve_application_data_root() / "trowel.db"
    return "trowel.db"


def create_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """打开 SQLite 数据库并设置行读取、WAL 和外键选项。

    Args:
        db_path: 明确的数据库路径；省略时使用 ``resolve_database_path`` 的运行环境
            规则。

    Returns:
        已启用字典式行读取、WAL 和外键约束的 SQLite 连接。
    """

    resolved_path = resolve_database_path(db_path)
    if _SERIALIZE_SQLITE_OPEN_CLOSE:
        with _SQLITE_OPEN_CLOSE_LOCK:
            conn = _open_connection(resolved_path)
    else:
        conn = _open_connection(resolved_path)
    conn.row_factory = sqlite3.Row
    # WAL 允许读取与写入并行；外键检查是 SQLite 的连接级开关。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _restrict_database_permissions(resolved_path)
    return conn


def _restrict_database_permissions(db_path: str | Path) -> None:
    """把主库及已创建的 WAL 辅助文件收紧为仅当前用户可读写。

    Args:
        db_path: 已成功打开的 SQLite 文件路径；内存库和 SQLite URI 会跳过。
    """

    raw_path = str(db_path)
    if raw_path == ":memory:" or raw_path.startswith("file:"):
        return
    private_mode = stat.S_IRUSR | stat.S_IWUSR
    for path in (
        Path(db_path),
        Path(f"{raw_path}-wal"),
        Path(f"{raw_path}-shm"),
    ):
        if path.exists():
            path.chmod(private_mode)
