from pathlib import Path

import pytest

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    init_event,
    line,
    result_ok as result_event,
)
from trowel_py.memory.sessions_repo import SessionRecord

__all__ = [
    "CapturingRegistrar",
    "FailingUpdateRegistrar",
    "FakeProc",
    "FakeSpawner",
    "init_event",
    "line",
    "patch_memory_root",
    "result_event",
]


class CapturingRegistrar:
    def __init__(self) -> None:
        self.registered: list[SessionRecord] = []
        self.completed: list[tuple[str, int]] = []

    def register(self, record: SessionRecord) -> None:
        self.registered.append(record)

    def update_completed(
        self,
        cc_session_id: str,
        completed_bytes: int,
        when: str | None = None,
    ) -> None:
        self.completed.append((cc_session_id, completed_bytes))


class FailingUpdateRegistrar(CapturingRegistrar):
    def update_completed(
        self,
        cc_session_id: str,
        completed_bytes: int,
        when: str | None = None,
    ) -> None:
        raise OSError("registry unavailable")


def patch_memory_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    return tmp_path
