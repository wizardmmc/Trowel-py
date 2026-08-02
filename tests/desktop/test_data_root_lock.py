"""验证 Desktop 数据根在整个 sidecar 生命周期内只能有一个写入者。"""

from pathlib import Path

import pytest

from trowel_py.desktop.data_root_lock import DataRootInUseError, hold_data_root_lock


def test_second_owner_cannot_hold_same_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"

    with hold_data_root_lock(data_root, owner="packaged"):
        with pytest.raises(DataRootInUseError, match="already in use"):
            with hold_data_root_lock(data_root, owner="canonical-dev"):
                pass


def test_data_root_lock_is_released_when_owner_exits(tmp_path: Path) -> None:
    data_root = tmp_path / "data"

    with hold_data_root_lock(data_root, owner="packaged"):
        pass

    with hold_data_root_lock(data_root, owner="canonical-dev") as lock_path:
        assert lock_path == data_root / ".data-root.lock"
