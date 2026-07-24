from pathlib import Path
from uuid import uuid4

import pytest

from trowel_py.cc_host import session_scan

from .support import VALID_SESSION_ID, user_event, write_events


def test_excludes_non_uuid_filename(fake_projects: Path) -> None:
    write_events(fake_projects / "not-a-uuid.jsonl", [user_event("hi")])

    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_excludes_sidechain_first_line(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [
            {
                "type": "user",
                "isSidechain": True,
                "message": {"role": "user", "content": "subagent message"},
            }
        ],
    )

    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_excludes_metadata_only(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [
            {"type": "queue-operation", "operation": "enqueue"},
            {"type": "attachment", "uuid": "attachment-1"},
        ],
    )

    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_count_filters_all_resume_rules(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [user_event("valid one")],
    )
    write_events(
        fake_projects / f"{uuid4()}.jsonl",
        [user_event("valid two")],
    )
    write_events(fake_projects / "not-uuid.jsonl", [user_event("bad name")])
    write_events(
        fake_projects / f"{uuid4()}.jsonl",
        [
            {
                "type": "user",
                "isSidechain": True,
                "message": {"role": "user", "content": "subagent"},
            }
        ],
    )
    write_events(
        fake_projects / f"{uuid4()}.jsonl",
        [{"type": "queue-operation"}],
    )
    (fake_projects / "notes.md").write_text("not a session\n", encoding="utf-8")

    assert session_scan.count_sessions("/workdir") == 2
    assert len(session_scan.list_sessions("/workdir")) == 2


def test_title_extraction_failure_does_not_crash_list(
    fake_projects: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [user_event("good")],
    )
    malformed_id = str(uuid4())
    (fake_projects / f"{malformed_id}.jsonl").write_text(
        "garbage\n",
        encoding="utf-8",
    )

    def extract_title(path: Path) -> str:
        if path.stem == VALID_SESSION_ID:
            return "good"
        raise ValueError("malformed")

    monkeypatch.setattr(session_scan, "_extract_title", extract_title)

    sessions = session_scan.list_sessions("/workdir")

    assert [session.title for session in sessions] == ["good"]
