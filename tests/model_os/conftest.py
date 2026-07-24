"""Model OS Store 测试夹具；每个测试使用独立临时 SQLite 文件。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.store import ModelOsStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "model_os.db"


@pytest.fixture
def store(db_path: Path) -> ModelOsStore:
    opened = ModelOsStore(db_path)
    opened.open()
    yield opened
    opened.close()
