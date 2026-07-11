"""tests for dictionary L0/L1 regeneration (slice-040-c C-3)."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.dictionary import (
    derive_dictionary_full,
    rebuild_dictionary,
    sync_dictionary_incremental,
)
from trowel_py.memory.store import MemoryStore


class _FakeProvider:
    """Returns a canned cluster/assign JSON."""

    def __init__(self, cluster_json: str, assign_json: str = "") -> None:
        self._cluster = cluster_json
        self._assign = assign_json
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if "分类器" in system_prompt:
            return self._assign or '{"assign":[]}'
        return self._cluster


def _write_note(root: Path, title: str, summary: str, tags=None, kind="fact") -> str:
    store = MemoryStore(root)
    return store.write_note({
        "type": "note", "title": title, "summary": summary,
        "tags": tags or [], "kind": kind, "verification": "verified",
        "confidence": "draft", "refs": 0, "last_ref": "", "retired": False,
    })


def test_derive_full_clusters_into_domains(tmp_path: Path) -> None:
    _write_note(tmp_path, "猫池号码供应", "骗号用猫池", ["telecom", "硬件"])
    _write_note(tmp_path, "接码平台", "接码平台供号", ["telecom"])
    _write_note(tmp_path, "Bun hash 坑", "Bun 算 hash 的坑", ["bun", "cc"])
    cluster = (
        '{"domains": [{"name":"telecom-fraud","description":"电信诈骗黑产",'
        '"triggers":"猫池,接码,骗号","note_ids":["猫池号码供应","接码平台"]},'
        '{"name":"claude-code","description":"CC 套壳坑","triggers":"Bun,hash",'
        '"note_ids":["Bun-hash-坑"]}]}'
    )
    out = derive_dictionary_full(tmp_path, _FakeProvider(cluster))
    assert "telecom-fraud" in out["L0"]
    assert "claude-code" in out["L0"]
    assert len(out["L1"]) == 2
    assert "猫池号码供应" in out["L1"]["telecom-fraud"]
    assert "Bun-hash-坑" in out["L1"]["claude-code"]


def test_derive_full_orphans_go_to_misc(tmp_path: Path) -> None:
    _write_note(tmp_path, "Note A", "a", [])
    _write_note(tmp_path, "Note B", "b", [])
    # LLM only assigns Note A; Note B is orphan
    cluster = '{"domains":[{"name":"d1","description":"x","triggers":"","note_ids":["Note-A"]}]}'
    out = derive_dictionary_full(tmp_path, _FakeProvider(cluster))
    assert "misc" in out["L1"]
    assert "Note-B" in out["L1"]["misc"]


def test_derive_full_llm_failure_fallback(tmp_path: Path) -> None:
    _write_note(tmp_path, "X", "x", [])
    out = derive_dictionary_full(tmp_path, _FakeProvider("not json at all"))
    # fallback: all in misc, never crash
    assert "misc" in out["L1"]
    assert "X" in out["L1"]["misc"]


def test_rebuild_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_note(tmp_path, "N", "n", [])
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["N"]}]}'
    out = rebuild_dictionary(tmp_path, apply=False, provider=_FakeProvider(cluster))
    assert out["apply"] is False
    assert not (tmp_path / "dictionary-L0.md").exists()
    assert not (tmp_path / "dictionary-L1").exists()


def test_rebuild_apply_writes_files(tmp_path: Path) -> None:
    _write_note(tmp_path, "N", "n", ["t"])
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"t","note_ids":["N"]}]}'
    out = rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    assert out["apply"] is True
    assert (tmp_path / "dictionary-L0.md").exists()
    assert (tmp_path / "dictionary-L1" / "d.md").exists()
    l0 = (tmp_path / "dictionary-L0.md").read_text(encoding="utf-8")
    assert "### d" in l0
    assert "触发词：t" in l0


def test_sync_incremental_appends_to_existing_domain(tmp_path: Path) -> None:
    _write_note(tmp_path, "Old", "old note", ["t"])
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"t","note_ids":["Old"]}]}'
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    # add a new note
    new_id = _write_note(tmp_path, "New Note", "new", ["t"])
    assign = f'{{"assign":[{{"note_id":"{new_id}","domain":"d"}}]}}'
    out = sync_dictionary_incremental(tmp_path, [new_id], _FakeProvider(cluster, assign))
    assert out["synced"] == 1
    l1 = (tmp_path / "dictionary-L1" / "d.md").read_text(encoding="utf-8")
    assert "Old" in l1
    assert "New Note" in l1


def test_sync_incremental_no_dictionary_falls_back_to_full(tmp_path: Path) -> None:
    _write_note(tmp_path, "N", "n", [])
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["N"]}]}'
    out = sync_dictionary_incremental(tmp_path, ["N"], _FakeProvider(cluster))
    assert (tmp_path / "dictionary-L0.md").exists()  # full rebuild happened


def test_sync_misc_domain_added_to_l0(tmp_path: Path) -> None:
    """codex P2-2: a new note sent to misc must surface in L0 so search finds it."""
    _write_note(tmp_path, "Old", "old", ["t"])
    cluster = '{"domains":[{"name":"d","description":"x","triggers":"t","note_ids":["Old"]}]}'
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    new_id = _write_note(tmp_path, "NewUnrelated", "new", [])
    assign = f'{{"assign":[{{"note_id":"{new_id}","domain":"misc"}}]}}'
    sync_dictionary_incremental(tmp_path, [new_id], _FakeProvider(cluster, assign))
    l0 = (tmp_path / "dictionary-L0.md").read_text(encoding="utf-8")
    assert "### misc" in l0  # misc added to L0 so search discovers its L1
    assert (tmp_path / "dictionary-L1" / "misc.md").exists()
