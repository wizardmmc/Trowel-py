"""Persist 的完整字段、幂等、附属产物与完成门禁。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from trowel_py.memory.draft import Draft, DraftDiary, DraftNote
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

_TODAY = "2026-07-09"


def _ctx(sid: str = "s1", review_date: str = _TODAY) -> PersistContext:
    return PersistContext(
        segment_id=f"{sid}:0:end",
        cc_session_id=sid,
        workdir="/proj",
        registered_at="2026-07-09T10:00:00",
        review_date=review_date,
        source_jsonl=f"/jsonl/{sid}.jsonl",
    )


def _note(**over) -> DraftNote:
    base = DraftNote(
        title="缓存坑",
        summary="build 没生效先查缓存",
        body="根因实测内容",
        verification="event-data-supported",
        verification_reason="会话内直接观测",
        pain=3,
        pain_reason="浪费两小时",
        conflicts_with=("existing-note",),
        kind="gotcha",
    )
    return replace(base, **over) if over else base


def _draft(**over) -> Draft:
    base = Draft(
        notes=(_note(),),
        diary=(DraftDiary(date=_TODAY, events="卡两小时在缓存"),),
        reflection="温故反思正文",
        escalate_to_human=("未解的问题",),
    )
    return replace(base, **over) if over else base


def test_persist_lands_all_rationale_fields(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    report = persist_draft(store, _draft(), _ctx())
    [n] = store.load_notes()
    assert n.verification_reason == "会话内直接观测"
    assert n.pain_reason == "浪费两小时"
    assert n.conflicts_with == ("existing-note",)
    assert n.kind == "gotcha"
    assert report.ok


def test_persist_lands_provenance_fields(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    [n] = store.load_notes()
    assert "s1" in n.source_sessions
    assert n.content_hash


def test_persist_writes_episode_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    ep = tmp_path / "episodes" / "s1.md"
    assert ep.exists()
    assert "卡两小时在缓存" in ep.read_text(encoding="utf-8")


def test_persist_writes_reflection_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    refl = tmp_path / "meta" / "reflections" / "s1.md"
    assert refl.exists()
    assert "温故反思正文" in refl.read_text(encoding="utf-8")


def test_persist_skips_empty_reflection(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(reflection=""), _ctx("s1"))
    assert not (tmp_path / "meta" / "reflections" / "s1.md").exists()


def test_persist_writes_escalation_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    esc = tmp_path / "meta" / "escalations" / "s1.md"
    assert esc.exists()
    assert "未解的问题" in esc.read_text(encoding="utf-8")


def test_persist_skips_empty_escalation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(escalate_to_human=()), _ctx("s1"))
    assert not (tmp_path / "meta" / "escalations" / "s1.md").exists()


def test_persist_idempotent_same_content_no_dup(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    # 模拟已写产品但尚未写完成门禁的中断。
    _clear_manifests(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    assert len(store.load_notes()) == 1


def test_persist_rejudge_updates_mutable_not_new(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    _clear_manifests(tmp_path)
    rejudged = _draft(notes=(_note(verification="verified", pain=8),))
    report = persist_draft(store, rejudged, _ctx("s1"))
    notes = store.load_notes()
    assert len(notes) == 1
    [n] = notes
    assert n.verification == "verified"
    assert n.pain == 8
    assert len(report.notes_created) == 0
    assert len(report.notes_updated) == 1


def test_persist_different_content_creates_new(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    _clear_manifests(tmp_path)
    persist_draft(
        store,
        _draft(notes=(_note(title="完全不同的结论", body="新正文"),)),
        _ctx("s1"),
    )
    assert len(store.load_notes()) == 2


def test_persist_manifest_written_last(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    report = persist_draft(store, _draft(), _ctx("s1"))
    manifest = _read_manifest(tmp_path, "s1:0:end")
    assert manifest is not None
    assert manifest["cc_session_id"] == "s1"
    assert manifest["segment_id"] == "s1:0:end"
    assert "episode_file" in manifest
    assert report.ok


def test_persist_manifest_intact_is_noop(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    mtime_before = (tmp_path / "episodes" / "s1.md").stat().st_mtime_ns
    report = persist_draft(store, _draft(), _ctx("s1"))
    mtime_after = (tmp_path / "episodes" / "s1.md").stat().st_mtime_ns
    assert mtime_before == mtime_after
    assert len(report.notes_created) == 0
    assert len(report.notes_updated) == 0
    assert report.ok


def test_persist_mid_failure_leaves_no_manifest(tmp_path: Path, monkeypatch) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    _clear_manifests(tmp_path)

    def boom(_ctx, _d):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_episode", boom)
    with pytest.raises(OSError):
        persist_draft(store, _draft(), _ctx("s1"))
    assert not list((tmp_path / "meta" / "persisted-segments").glob("*.json"))
    assert len(store.load_notes()) == 1


def test_persist_inferred_untested_never_stable(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(
        store,
        _draft(notes=(_note(verification="inferred-untested"),)),
        _ctx("s1"),
    )
    [n] = store.load_notes()
    assert n.verification == "inferred-untested"


def test_persist_never_writes_core(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    assert not (tmp_path / "core.md").exists()
    assert store.load_core() == ""


def test_persist_verification_counts(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    report = persist_draft(
        store,
        _draft(
            notes=(
                _note(title="a", verification="verified"),
                _note(title="b", verification="inferred-untested"),
                _note(title="c", verification="inferred-untested"),
            )
        ),
        _ctx("s1"),
    )
    assert report.verification_counts == {
        "verified": 1,
        "inferred-untested": 2,
    }


def _clear_manifests(root: Path) -> None:
    seg_dir = root / "meta" / "persisted-segments"
    if seg_dir.exists():
        for f in seg_dir.glob("*.json"):
            f.unlink()


def _read_manifest(root: Path, segment_id: str) -> dict | None:
    p = root / "meta" / "persisted-segments" / f"{segment_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
