from __future__ import annotations

import json
from pathlib import Path

from tests.memory.profile_distill.support import (
    ERROR,
    FINISHED,
    VALID_DRAFT,
    FakeHost,
    draft_item,
    fake_host_factory,
    seed_session,
    session_record,
)
from trowel_py.memory.profile_distill.state import load_processed, mark_processed
from trowel_py.memory.profile_distill_job import run_daily_distill
from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    load_suggestions,
)
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)


async def test_run_daily_distill_appends_and_marks(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
        date_str="2026-07-15",
    )
    pending = load_suggestions(root)
    assert len(pending) == 1
    assert pending[0].body == "熟悉缓存一致性 / 并发调试"
    processed = load_processed(root)
    assert "s1" in processed
    assert processed["s1"].end_offset == 1000


async def test_run_daily_distill_failed_session_not_marked(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([ERROR], draft_text=None),
        date_str="2026-07-15",
    )
    assert load_suggestions(root) == []
    assert load_processed(root) == {}


async def test_run_daily_distill_skips_already_processed(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    mark_processed(root, "s1", end_offset=1000, at="2026-07-14T02:50:00")

    calls: list[str] = []

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session.cc_session_id)
        return FakeHost([FINISHED])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )
    assert calls == []
    assert load_suggestions(root) == []


async def test_run_daily_distill_redistills_new_offset(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=2000)
    mark_processed(root, "s1", end_offset=1000, at="2026-07-14T02:50:00")

    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
        date_str="2026-07-15",
    )
    assert len(load_suggestions(root)) == 1
    assert load_processed(root)["s1"].end_offset == 2000


async def test_run_daily_distill_excludes_review_and_distill_kinds(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.register(session_record("user"))
        repo.register(session_record("rev"))
        repo.register(session_record("dist"))
        repo.update_completed("user", 1000)
        repo.update_completed("rev", 1000)
        repo.update_completed("dist", 1000)
        conn.execute(
            "UPDATE sessions SET session_kind='review' WHERE cc_session_id='rev'"
        )
        conn.execute(
            "UPDATE sessions SET session_kind='distill' WHERE cc_session_id='dist'"
        )
        conn.commit()
    finally:
        conn.close()

    calls: list[str] = []

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session.cc_session_id)
        (workdir / "suggestions-draft.json").write_text(
            VALID_DRAFT,
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    await run_daily_distill(
        root,
        "http://x",
        host_factory=factory,
        date_str="2026-07-15",
    )
    assert calls == ["user"]


async def test_run_daily_distill_advances_when_all_gated_away(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    all_too_long = json.dumps({"suggestions": [draft_item(body="字" * 61)]})
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], all_too_long),
        date_str="2026-07-17",
    )
    assert load_suggestions(root) == []
    assert load_processed(root)["s1"].end_offset == 1000


async def test_run_daily_distill_bad_dim_does_not_advance(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    bad_dim = json.dumps({"suggestions": [draft_item(dim="personality")]})
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], bad_dim),
        date_str="2026-07-17",
    )
    assert load_processed(root) == {}
    assert load_suggestions(root) == []


async def test_run_daily_distill_dedup_ignores_v1_queue(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    seed_session(root, "s1", completed=1000)
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "v1-old",
                        "dimension": "methodology",
                        "body": "把 commit 写清楚这种很长的 v1 methodology 描述含例子",
                        "sources": ["old-cc"],
                        "date": "2026-07-01",
                        "status": "pending",
                    }
                ],
                "updated": "2026-07-01",
            }
        ),
        encoding="utf-8",
    )
    v2_draft = json.dumps(
        {"suggestions": [draft_item(body="commit 要让外行看懂", dim="methodology")]}
    )
    await run_daily_distill(
        root,
        "http://x",
        host_factory=fake_host_factory([FINISHED], v2_draft),
        date_str="2026-07-17",
    )
    loaded = load_suggestions(root)
    assert {suggestion.id for suggestion in loaded} == {"v1-old"} | {
        suggestion.id
        for suggestion in loaded
        if suggestion.policy_version == PROFILE_DISTILL_POLICY_VERSION
    }
    v2 = [
        suggestion
        for suggestion in loaded
        if suggestion.policy_version == PROFILE_DISTILL_POLICY_VERSION
    ]
    assert len(v2) == 1
    assert v2[0].body == "commit 要让外行看懂"
