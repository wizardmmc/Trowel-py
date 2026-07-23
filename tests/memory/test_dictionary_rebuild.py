"""验证 dictionary 的 staging 校验、原子替换与失败回滚。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trowel_py.memory.dictionary import rebuild_dictionary
from trowel_py.memory.dictionary_check import check_dictionary
from trowel_py.memory.dictionary_index import publish, render
from trowel_py.memory.dictionary_state import load_state
from trowel_py.memory.store import MemoryStore


class _FakeProvider:
    def __init__(self, cluster_json: str) -> None:
        self._c = cluster_json

    def complete(self, sys_p: str, user_p: str) -> str:
        return self._c


class _BoomProvider:
    def complete(self, sys_p: str, user_p: str) -> str:
        raise RuntimeError("provider 529")


def _note(root: Path, title: str) -> str:
    return MemoryStore(root).write_note({
        "type": "note", "title": title, "summary": "s", "tags": [],
        "kind": "fact", "verification": "verified", "refs": 0, "last_ref": "",
    })


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    l0 = root / "dictionary-L0.md"
    if l0.exists():
        out["L0"] = hashlib.sha256(l0.read_bytes()).hexdigest()
    l1d = root / "dictionary-L1"
    if l1d.exists():
        for p in sorted(l1d.glob("*.md")):
            out[f"L1/{p.stem}"] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_rebuild_apply_yields_consistent_index_and_state(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    _note(tmp_path, "Beta")
    cluster = (
        '{"domains":[{"name":"d","description":"x","triggers":"t",'
        '"note_ids":["Alpha","Beta"]}]}'
    )
    out = rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    assert out["apply"] is True
    assert "error" not in out
    report = check_dictionary(tmp_path)
    assert report["status"] == "consistent", report
    state = load_state(tmp_path)
    assert state.status == "consistent"
    assert state.source_hash == out["source_hash"]


def test_rebuild_apply_clears_orphan_l1(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    l1d = tmp_path / "dictionary-L1"
    l1d.mkdir(parents=True)
    (l1d / "ghost.md").write_text("# ghost\n", encoding="utf-8")
    (tmp_path / "dictionary-L0.md").write_text(
        "### ghost（1 条 → read dictionary-L1/ghost.md）\n", encoding="utf-8"
    )
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["Alpha"]}]}'
    out = rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    assert out["apply"] is True
    assert not (l1d / "ghost.md").exists()
    assert (l1d / "d.md").exists()
    assert check_dictionary(tmp_path)["orphan_l1_files"] == []


def test_rebuild_dry_run_writes_nothing(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["Alpha"]}]}'
    out = rebuild_dictionary(tmp_path, apply=False, provider=_FakeProvider(cluster))
    assert out["apply"] is False
    assert out["check"]["status"] == "consistent"
    assert not (tmp_path / "dictionary-L0.md").exists()
    assert not (tmp_path / "dictionary-L1").exists()
    assert load_state(tmp_path).status == "missing"


def test_rebuild_provider_failure_keeps_old_index_and_marks_stale(
    tmp_path: Path,
) -> None:
    _note(tmp_path, "Alpha")
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["Alpha"]}]}'
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    before = _snapshot(tmp_path)
    out = rebuild_dictionary(tmp_path, apply=True, provider=_BoomProvider())
    assert out.get("error") == "derive_failed"
    assert _snapshot(tmp_path) == before
    assert load_state(tmp_path).status == "stale"


def test_rebuild_staging_inconsistent_keeps_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _note(tmp_path, "Alpha")
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["Alpha"]}]}'
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    before = _snapshot(tmp_path)
    def dup_cluster(notes_with_id, provider):  # noqa: ANN001
        stem = notes_with_id[0][0]
        return [
            {"name": "d1", "description": "x", "triggers": "", "note_ids": [stem]},
            {"name": "d2", "description": "x", "triggers": "", "note_ids": [stem]},
        ]

    monkeypatch.setattr(render, "_cluster_notes", dup_cluster)
    out = rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(""))
    assert out.get("error") == "staging_inconsistent"
    assert _snapshot(tmp_path) == before
    assert load_state(tmp_path).status == "stale"


def test_atomic_replace_midswap_failure_restores_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dictionary-L0.md").write_text("OLD L0", encoding="utf-8")
    l1d = tmp_path / "dictionary-L1"
    l1d.mkdir()
    (l1d / "old.md").write_text("OLD L1", encoding="utf-8")
    before = _snapshot(tmp_path)

    # 在 L1 已换代后让 L0 replace 失败，必须恢复同一代旧索引。
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise OSError("injected at os.replace")

    monkeypatch.setattr(publish.os, "replace", boom)
    with pytest.raises(OSError):
        publish.atomic_replace(
            tmp_path,
            "NEW L0",
            {"new": "NEW L1\n"},
        )
    assert _snapshot(tmp_path) == before


def test_rebuild_dry_run_failure_writes_no_state(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    out = rebuild_dictionary(tmp_path, apply=False, provider=_BoomProvider())
    assert out.get("error") == "derive_failed"
    assert load_state(tmp_path).status == "missing"
