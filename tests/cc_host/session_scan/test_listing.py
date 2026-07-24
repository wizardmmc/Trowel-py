from pathlib import Path
from uuid import uuid4

import pytest

from trowel_py.cc_host import session_scan

from .support import VALID_SESSION_ID, write_timed_sessions


def test_sort_most_recent_first(fake_projects: Path) -> None:
    newer_id = str(uuid4())
    write_timed_sessions(
        fake_projects,
        [(VALID_SESSION_ID, 1), (newer_id, 2)],
    )

    sessions = session_scan.list_sessions("/workdir")

    assert [session.cc_session_id for session in sessions] == [
        newer_id,
        VALID_SESSION_ID,
    ]


def test_empty_when_projects_dir_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_scan,
        "cc_projects_root",
        lambda: tmp_path / "missing",
    )

    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_limit_caps_to_n_most_recent(fake_projects: Path) -> None:
    session_ids = [str(uuid4()) for _ in range(5)]
    write_timed_sessions(
        fake_projects,
        list(zip(session_ids, [1, 2, 3, 4, 5], strict=True)),
    )

    sessions = session_scan.list_sessions("/workdir", limit=3)

    assert [session.cc_session_id for session in sessions] == [
        session_ids[4],
        session_ids[3],
        session_ids[2],
    ]


def test_limit_none_returns_all(fake_projects: Path) -> None:
    session_ids = [str(uuid4()) for _ in range(5)]
    write_timed_sessions(
        fake_projects,
        list(zip(session_ids, [1, 2, 3, 4, 5], strict=True)),
    )

    sessions = session_scan.list_sessions("/workdir")

    assert [session.cc_session_id for session in sessions] == list(
        reversed(session_ids)
    )


def test_limit_larger_than_count_returns_all(fake_projects: Path) -> None:
    session_ids = [str(uuid4()), str(uuid4())]
    write_timed_sessions(
        fake_projects,
        list(zip(session_ids, [1, 2], strict=True)),
    )

    sessions = session_scan.list_sessions("/workdir", limit=10)

    assert [session.cc_session_id for session in sessions] == list(
        reversed(session_ids)
    )
