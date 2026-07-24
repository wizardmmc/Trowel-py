"""Episode segment 写入与 upsert。"""

from pathlib import Path

from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.store import MemoryStore

from .support import _ctx


def test_write_episode_creates_per_session_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), (DraftDiary(date="2026-07-09", events="s1 body"),))
    store.write_episode(_ctx("s2"), (DraftDiary(date="2026-07-09", events="s2 body"),))
    episodes = sorted((tmp_path / "episodes").glob("*.md"))
    assert [p.stem for p in episodes] == ["s1", "s2"]


def test_three_sessions_same_day_do_not_overwrite(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    for i, sid in enumerate(["s1", "s2", "s3"]):
        store.write_episode(
            _ctx(sid, workdir=f"/proj{i}", registered_at=f"2026-07-09T1{i}:00:00"),
            (DraftDiary(date="2026-07-09", events=f"session {sid} 独有经历"),),
        )
    episodes = sorted((tmp_path / "episodes").glob("*.md"))
    assert len(episodes) == 3
    texts = [p.read_text(encoding="utf-8") for p in episodes]

    assert any("session s1 独有经历" in t for t in texts)
    assert any("session s2 独有经历" in t for t in texts)
    assert any("session s3 独有经历" in t for t in texts)


def test_write_episode_frontmatter_carries_provenance(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", workdir="/proj", review_date="2026-07-09"),
        (DraftDiary(date="2026-07-09", events="事件"),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "cc_session_id: s1" in text
    assert "workdir: /proj" in text
    assert "review_date: '2026-07-09'" in text or "review_date: 2026-07-09" in text
    assert "activity_dates" in text
    assert "source_jsonl: /jsonl/s1.jsonl" in text
    assert "segments:" in text
    assert "segment_id: 's1:0:end'" in text or "segment_id: s1:0:end" in text


def test_segment_meta_carries_activity_dates_and_basis(tmp_path: Path) -> None:
    from trowel_py.memory.store import _split_frontmatter

    store = MemoryStore(tmp_path)
    ctx = _ctx(
        "s1",
        activity_dates=("2026-07-16", "2026-07-17"),
        date_basis="jsonl_timestamp",
        processed_date="2026-07-18",
    )
    store.write_episode(
        ctx,
        (
            DraftDiary(date="2026-07-16", events="d16"),
            DraftDiary(date="2026-07-17", events="d17"),
        ),
    )
    fm, _body = _split_frontmatter(
        (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    )
    seg = fm["segments"][0]
    assert seg["activity_dates"] == ["2026-07-16", "2026-07-17"]
    assert seg["date_basis"] == "jsonl_timestamp"
    assert seg["processed_date"] == "2026-07-18"


def test_write_episode_segment_upsert_preserves_others(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100"),
        (DraftDiary(date="2026-07-09", events="第一段经历"),),
    )
    store.write_episode(
        _ctx("s1", segment_id="s1:200:300"),
        (DraftDiary(date="2026-07-09", events="第二段经历"),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "第一段经历" in text
    assert "第二段经历" in text


def test_write_episode_same_segment_replaces_only_it(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100"),
        (DraftDiary(date="2026-07-09", events="原始"),),
    )
    store.write_episode(
        _ctx("s1", segment_id="s1:0:100"),
        (DraftDiary(date="2026-07-09", events="更新后"),),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "更新后" in text
    assert "原始" not in text


def test_write_episode_empty_diary_marks_empty_reason(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(_ctx("s1"), ())
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")
    assert "empty_reason" in text


def test_write_episode_multi_date_diary_in_one_segment(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_episode(
        _ctx("s1"),
        (
            DraftDiary(date="2026-07-09", events="后一天"),
            DraftDiary(date="2026-07-08", events="前一天"),
        ),
    )
    text = (tmp_path / "episodes" / "s1.md").read_text(encoding="utf-8")

    assert text.index("前一天") < text.index("后一天")
    assert "2026-07-08" in text and "2026-07-09" in text
