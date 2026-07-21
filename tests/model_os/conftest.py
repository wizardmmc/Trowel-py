"""Test fixtures for the Model OS store (slice-084).

Every test gets a brand-new SQLite file under a temp dir — the spec's pass
criterion 5 demands "old DB does not exist → safe bootstrap; tests use temp
dirs". No test ever touches ``trowel.db`` or the user's ``~/.trowel``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.store import ModelOsStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh model_os.db path inside the test's temp dir."""

    return tmp_path / "model_os.db"


@pytest.fixture
def store(db_path: Path) -> ModelOsStore:
    """An opened, bootstrapped store backed by a temp db file.

    Closes the connection on teardown so WAL/SHM sidecar files don't linger.
    """

    opened = ModelOsStore(db_path)
    opened.open()
    yield opened
    opened.close()
