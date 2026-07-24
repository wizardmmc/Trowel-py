"""验证 dictionary L0/L1 的派生与公开重建入口。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.dictionary import (
    derive_dictionary_full,
    rebuild_dictionary,
)
from trowel_py.memory.store import MemoryStore


class _FakeProvider:
    def __init__(self, cluster_json: str) -> None:
        self._cluster = cluster_json
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return self._cluster


def _write_note(root: Path, title: str, summary: str, tags=None, kind="fact") -> str:
    store = MemoryStore(root)
    return store.write_note(
        {
            "type": "note",
            "title": title,
            "summary": summary,
            "tags": tags or [],
            "kind": kind,
            "verification": "verified",
            "confidence": "draft",
            "refs": 0,
            "last_ref": "",
            "retired": False,
        }
    )


def test_derive_full_clusters_into_domains(tmp_path: Path) -> None:
    _write_note(tmp_path, "缓存失效窗口", "缓存切换存在短暂失效", ["cache", "一致性"])
    _write_note(tmp_path, "一致性策略", "按业务选择一致性级别", ["cache"])
    _write_note(tmp_path, "Bun hash 坑", "Bun 算 hash 的坑", ["bun", "cc"])
    cluster = (
        '{"domains": [{"name":"cache-consistency","description":"缓存一致性实践",'
        '"triggers":"缓存,一致性","note_ids":["缓存失效窗口","一致性策略"]},'
        '{"name":"claude-code","description":"CC 套壳坑","triggers":"Bun,hash",'
        '"note_ids":["Bun-hash-坑"]}]}'
    )
    out = derive_dictionary_full(tmp_path, _FakeProvider(cluster))
    assert "cache-consistency" in out["L0"]
    assert "claude-code" in out["L0"]
    assert len(out["L1"]) == 2
    assert "缓存失效窗口" in out["L1"]["cache-consistency"]
    assert "Bun-hash-坑" in out["L1"]["claude-code"]


def test_derive_full_orphans_go_to_misc(tmp_path: Path) -> None:
    _write_note(tmp_path, "Note A", "a", [])
    _write_note(tmp_path, "Note B", "b", [])
    cluster = '{"domains":[{"name":"d1","description":"x","triggers":"","note_ids":["Note-A"]}]}'
    out = derive_dictionary_full(tmp_path, _FakeProvider(cluster))
    assert "misc" in out["L1"]
    assert "Note-B" in out["L1"]["misc"]


def test_derive_full_excludes_inactive_notes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    live = _write_note(tmp_path, "Live", "active note", ["t"])
    dead = _write_note(tmp_path, "Dead", "superseded note", ["t"])
    store.update_note_fields(dead, {"status": "superseded"})
    cluster = (
        '{"domains":[{"name":"d","description":"x","triggers":"t",'
        '"note_ids":["' + live + '","' + dead + '"]}]}'
    )
    out = derive_dictionary_full(tmp_path, _FakeProvider(cluster))
    rendered = "\n".join(out["L1"].values())
    assert live in rendered
    assert dead not in rendered


def test_derive_full_llm_failure_fallback(tmp_path: Path) -> None:
    _write_note(tmp_path, "X", "x", [])
    out = derive_dictionary_full(tmp_path, _FakeProvider("not json at all"))
    assert "misc" in out["L1"]
    assert "X" in out["L1"]["misc"]


def test_rebuild_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_note(tmp_path, "N", "n", [])
    cluster = (
        '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["N"]}]}'
    )
    out = rebuild_dictionary(tmp_path, apply=False, provider=_FakeProvider(cluster))
    assert out["apply"] is False
    assert not (tmp_path / "dictionary-L0.md").exists()
    assert not (tmp_path / "dictionary-L1").exists()


def test_rebuild_apply_writes_files(tmp_path: Path) -> None:
    _write_note(tmp_path, "N", "n", ["t"])
    cluster = (
        '{"domains":[{"name":"d","description":"x","triggers":"t","note_ids":["N"]}]}'
    )
    out = rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(cluster))
    assert out["apply"] is True
    assert (tmp_path / "dictionary-L0.md").exists()
    assert (tmp_path / "dictionary-L1" / "d.md").exists()
    l0 = (tmp_path / "dictionary-L0.md").read_text(encoding="utf-8")
    assert "### d" in l0
    assert "触发词：t" in l0
