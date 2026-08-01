from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trowel_py.agent_host.workspaces import (
    RecentWorkspaceStore,
    WorkspaceUnavailableError,
)


def test_recent_workspaces_persist_in_latest_first_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    moments = iter(
        [
            datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 8, 1, 10, 1, tzinfo=UTC),
            datetime(2026, 8, 1, 10, 2, tzinfo=UTC),
        ]
    )
    database = tmp_path / "workspaces.db"
    store = RecentWorkspaceStore(database, clock=lambda: next(moments))

    store.remember(str(first))
    store.remember(str(second))
    store.remember(str(first))

    reopened = RecentWorkspaceStore(database)
    recent = reopened.list_recent()
    assert [item.path for item in recent] == [str(first), str(second)]
    assert recent[0].last_opened_at > recent[1].last_opened_at
    assert all(item.available for item in recent)


def test_recent_workspaces_enforce_limit_after_deduplication(tmp_path: Path) -> None:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    counter = 0

    def clock() -> datetime:
        nonlocal counter
        counter += 1
        return base + timedelta(seconds=counter)

    store = RecentWorkspaceStore(tmp_path / "workspaces.db", limit=2, clock=clock)
    paths = [tmp_path / name for name in ("one", "two", "three")]
    for path in paths:
        path.mkdir()
        store.remember(str(path))

    assert [item.path for item in store.list_recent()] == [
        str(paths[2]),
        str(paths[1]),
    ]


def test_remember_rejects_a_missing_directory(tmp_path: Path) -> None:
    store = RecentWorkspaceStore(tmp_path / "workspaces.db")

    with pytest.raises(WorkspaceUnavailableError, match="does not exist"):
        store.remember(str(tmp_path / "missing"))
