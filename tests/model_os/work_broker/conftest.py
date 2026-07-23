from pathlib import Path

import pytest

from trowel_py.model_os.work_broker import WorkBroker
from tests.model_os.work_broker._support import FakeClock, _policy


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "workbroker.db"


@pytest.fixture
def broker(db_path: Path, clock: FakeClock) -> WorkBroker:

    b = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b.open()
    yield b
    b.close()
