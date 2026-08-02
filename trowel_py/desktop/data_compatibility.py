"""阻止开发代码先于正式 App 升级长期数据格式。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from trowel_py.db.migrate import pending_migration_names

DesktopDataMode = Literal["packaged", "canonical-dev", "isolated-dev"]


class DataFormatUpgradeRequiredError(RuntimeError):
    """表示 canonical dev 会执行正式 App 尚未应用的数据迁移。"""


class LegacyDataMigrationRequiredError(RuntimeError):
    """表示上一候选已有业务数据但新 canonical 根尚未完成迁移。"""


def _legacy_candidate_requires_migration(data_root: Path) -> bool:
    """判断 canonical 根的父目录是否仍包含上一候选业务数据。"""

    manifest = data_root / "migration-manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return not (
            isinstance(payload, dict)
            and payload.get("version") == 1
            and payload.get("status") == "completed"
        )
    previous_app_root = data_root.parent
    return (previous_app_root / "trowel.db").exists() or (
        previous_app_root / "memory"
    ).exists()


def ensure_data_mode_compatible(data_root: Path, *, mode: DesktopDataMode) -> None:
    """在 canonical dev 打开现有数据库前拒绝待执行的 migration。

    打包版负责正式升级，隔离 dev 只写沙箱，因此这两种模式不受此门禁限制。
    canonical dev 面对尚未创建的数据库时也拒绝初始化；必须先由 packaged App
    建立或升级 schema，已有数据库则只能在没有待执行 migration 时继续。

    Args:
        data_root: 当前 Desktop Host 选择的业务数据根。
        mode: packaged、canonical-dev 或 isolated-dev。

    Raises:
        DataFormatUpgradeRequiredError: canonical dev 会改变现有主数据库 schema。
        sqlite3.Error: 已有路径不是可读的 SQLite 数据库。
    """

    if mode != "isolated-dev" and _legacy_candidate_requires_migration(data_root):
        raise LegacyDataMigrationRequiredError(
            "previous Trowel App data requires the offline data_migration command"
        )
    if mode != "canonical-dev":
        return
    database = data_root / "trowel.db"
    if not database.exists():
        raise DataFormatUpgradeRequiredError(
            "canonical dev cannot initialize App data. Start packaged Trowel first "
            "or use --isolated."
        )
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        pending = pending_migration_names(connection)
    finally:
        connection.close()
    if pending:
        rendered = ", ".join(pending)
        raise DataFormatUpgradeRequiredError(
            "canonical dev cannot upgrade App data; pending migrations: "
            f"{rendered}. Start packaged Trowel first or use --isolated."
        )
