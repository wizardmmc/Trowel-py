"""创建采用项目统一设置的 SQLite 连接。"""

import sqlite3
from pathlib import Path

from trowel_py.application_paths import (
    has_application_data_root_override,
    resolve_application_data_root,
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

    conn = sqlite3.connect(
        resolve_database_path(db_path),
        timeout=10,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # WAL 允许读取与写入并行；外键检查是 SQLite 的连接级开关。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
