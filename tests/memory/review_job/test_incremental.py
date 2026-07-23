from __future__ import annotations

import json
from pathlib import Path

from tests.memory.review_job.support import (
    FINISHED,
    VALID_DRAFT,
    FakeHost,
    factory,
    session,
)
from trowel_py.memory.compress import write_fallback_daily
from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.prompt import build_refine_prompt
from trowel_py.memory.review_job import run_daily_review
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore, _split_frontmatter
from trowel_py.memory.types import PersistContext


async def test_segment_manifest_id_carries_offsets(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("s1", "/proj1"))
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    manifests = sorted((memory_root / "meta" / "persisted-segments").glob("*.json"))
    assert [path.name for path in manifests] == ["s1:0:4096.json"]


async def test_resume_distills_only_new_incremental_range(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(session("s1", "/proj1"))
    repo.update_completed("s1", 2048)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )
    segment_dir = memory_root / "meta" / "persisted-segments"
    assert (segment_dir / "s1:0:2048.json").exists()

    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    assert (segment_dir / "s1:0:2048.json").exists()
    assert (segment_dir / "s1:2048:4096.json").exists()
    conn = open_sessions_db(memory_root)
    assert create_sessions_repository(conn).find_incremental() == []
    conn.close()


async def test_half_turn_without_completed_watermark_is_not_distilled(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    create_sessions_repository(conn).register(session("s1", "/proj1"))
    conn.close()
    calls: list[str] = []

    def create_host(session_record: SessionRecord, workdir: Path) -> FakeHost:
        calls.append(session_record.cc_session_id)
        return FakeHost([FINISHED])

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=create_host,
    )
    assert calls == []


async def test_fallback_daily_is_retried_without_new_segments(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    day = "2026-07-09"
    context = PersistContext(
        segment_id="existing:0:4096",
        cc_session_id="existing",
        workdir="/project",
        registered_at=f"{day}T10:00:00",
        review_date=day,
        source_jsonl="fixture.jsonl",
        activity_dates=(day,),
        date_basis="jsonl_timestamp",
        processed_date=day,
    )
    MemoryStore(memory_root).write_episode(
        context,
        (DraftDiary(date=day, outcomes=("完成了已有工作的关键实现",)),),
    )
    write_fallback_daily(memory_root, day)

    class Provider:
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            return json.dumps(
                {
                    "items": [
                        {
                            "type": "outcome",
                            "text": "完成了已有工作的关键实现",
                            "source": "S1",
                        }
                    ]
                }
            )

    await run_daily_review(
        memory_root=memory_root,
        date_str=day,
        host_factory=factory([]),
        provider=Provider(),
    )

    path = memory_root / "diary" / "daily" / f"{day}.md"
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert frontmatter and frontmatter["generation_status"] == "ok"
    assert "## 进展" in body


async def test_review_date_comes_from_run_not_session_start(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    conn = open_sessions_db(memory_root)
    repo = create_sessions_repository(conn)
    repo.register(
        SessionRecord(
            cc_session_id="s1",
            workdir="/project",
            date="2026-07-08",
            jsonl_path=session().jsonl_path,
            registered_at="2026-07-08T10:00:00",
        )
    )
    repo.update_completed("s1", 4096)
    conn.close()

    await run_daily_review(
        memory_root=memory_root,
        date_str="2026-07-09",
        host_factory=factory([FINISHED], VALID_DRAFT),
    )

    episode = (memory_root / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "2026-07-09" in episode
    assert (memory_root / "diary" / "daily" / "2026-07-09.md").exists()
    assert not (memory_root / "diary" / "daily" / "2026-07-08.md").exists()


async def test_refine_prompt_carries_only_incremental_range() -> None:
    whole = build_refine_prompt("fixture.jsonl", "tokens=0")
    assert "增量范围" not in whole

    incremental = build_refine_prompt(
        "fixture.jsonl",
        "tokens=0",
        start_offset=2048,
        end_offset=4096,
    )
    assert "增量范围" in incremental
    assert "[2048, 4096]" in incremental
