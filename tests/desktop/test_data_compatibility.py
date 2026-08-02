"""验证 dev 使用长期数据前不会抢先执行正式 App 尚未应用的迁移。"""

import sqlite3
from pathlib import Path

import pytest

from trowel_py.desktop.data_compatibility import (
    DataFormatUpgradeRequiredError,
    LegacyDataMigrationRequiredError,
    ensure_data_mode_compatible,
)


def test_canonical_dev_rejects_existing_database_with_pending_migration(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    connection = sqlite3.connect(data_root / "trowel.db")
    connection.execute("CREATE TABLE _migrations(name TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(DataFormatUpgradeRequiredError, match="001_create_cards.sql"):
        ensure_data_mode_compatible(data_root, mode="canonical-dev")


def test_canonical_dev_requires_packaged_app_to_initialize_data(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"

    with pytest.raises(DataFormatUpgradeRequiredError, match="initialize App data"):
        ensure_data_mode_compatible(empty_root, mode="canonical-dev")


def test_packaged_app_may_initialize_data(tmp_path: Path) -> None:
    existing_root = tmp_path / "existing"
    existing_root.mkdir()
    connection = sqlite3.connect(existing_root / "trowel.db")
    connection.execute("CREATE TABLE _migrations(name TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()
    ensure_data_mode_compatible(existing_root, mode="packaged")


@pytest.mark.parametrize("mode", ["packaged", "canonical-dev"])
def test_canonical_modes_refuse_to_skip_previous_app_data(
    tmp_path: Path,
    mode: str,
) -> None:
    previous_app_root = tmp_path / "Trowel"
    data_root = previous_app_root / "data"
    previous_app_root.mkdir()
    sqlite3.connect(previous_app_root / "trowel.db").close()

    with pytest.raises(LegacyDataMigrationRequiredError, match="data_migration"):
        ensure_data_mode_compatible(data_root, mode=mode)


def test_completed_manifest_allows_canonical_data_after_migration(
    tmp_path: Path,
) -> None:
    previous_app_root = tmp_path / "Trowel"
    data_root = previous_app_root / "data"
    data_root.mkdir(parents=True)
    sqlite3.connect(previous_app_root / "trowel.db").close()
    (data_root / "migration-manifest.json").write_text(
        '{"version":1,"status":"completed"}', encoding="utf-8"
    )

    ensure_data_mode_compatible(data_root, mode="packaged")
