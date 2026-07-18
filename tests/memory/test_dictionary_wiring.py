"""slice-064 wiring: ensure after batch ops, tidy supersede, mcp stale warning.

These exercise the connected behavior (not just the pure functions): that a
daily/tidy batch converges the index, that supersede drops the old note from
the default index (slice-064 user scenario), and that a stale index is served
with an observable MCP warning instead of silent incomplete recall.
"""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.dictionary import (
    ensure_dictionary_consistent,
    rebuild_dictionary,
)
from trowel_py.memory.dictionary_check import check_dictionary
from trowel_py.memory.dictionary_state import DictionaryState, save_state
from trowel_py.memory.mcp_server import handle_search
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


_ONE = '{"domains":[{"name":"d","description":"x","triggers":"","note_ids":["Alpha"]}]}'


def test_ensure_rebuilds_when_index_is_stale(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(_ONE))
    _note(tmp_path, "Beta")  # new note the index does not know about
    assert check_dictionary(tmp_path)["status"] == "stale"  # Beta missing
    two = ('{"domains":[{"name":"d","description":"x","triggers":"",'
           '"note_ids":["Alpha","Beta"]}]}')
    out = ensure_dictionary_consistent(tmp_path, _FakeProvider(two))
    assert out["dictionary_status"] == "consistent"
    assert out["rebuilt"] is True
    assert check_dictionary(tmp_path)["status"] == "consistent"


def test_ensure_noop_when_already_consistent(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(_ONE))
    # boom provider would fail if ensure rebuilt — it must NOT (already consistent)
    out = ensure_dictionary_consistent(tmp_path, _BoomProvider())
    assert out["rebuilt"] is False
    assert out["dictionary_status"] == "consistent"


def test_ensure_marks_stale_when_rebuild_fails(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(_ONE))
    _note(tmp_path, "Beta")  # makes it stale
    out = ensure_dictionary_consistent(tmp_path, _BoomProvider())
    assert out["dictionary_status"] == "stale"
    # note facts are NOT rolled back (C-7): both notes still on disk
    assert (tmp_path / "notes" / "Alpha.md").exists()
    assert (tmp_path / "notes" / "Beta.md").exists()


def test_tidy_supersede_drops_old_note_from_default_index(
    tmp_path: Path,
) -> None:
    """slice-064 user scenario: supersede → old entry leaves the default L1."""
    from trowel_py.memory.tidy import TidyOperation, TidyPlan, apply_plan

    store = MemoryStore(tmp_path)
    old = _note(tmp_path, "OldClaim")
    new = _note(tmp_path, "NewClaim")
    store.update_note_fields(old, {"memory_id": "mid-old"})
    store.update_note_fields(new, {"memory_id": "mid-new"})

    both = ('{"domains":[{"name":"d","description":"x","triggers":"",'
            '"note_ids":["OldClaim","NewClaim"]}]}')
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(both))

    plan = TidyPlan(
        plan_id="p1", source_snapshot={},
        operations=(TidyOperation(
            type="supersede", target="mid-old", by="mid-new", reason="r"
        ),),
    )
    apply_plan(tmp_path, plan)  # old → superseded

    # the index still references the now-superseded OldClaim → stale → rebuild
    after_supersede = ('{"domains":[{"name":"d","description":"x","triggers":"",'
                       '"note_ids":["NewClaim"]}]}')
    out = ensure_dictionary_consistent(tmp_path, _FakeProvider(after_supersede))
    assert out["dictionary_status"] == "consistent"
    l1 = (tmp_path / "dictionary-L1" / "d.md").read_text(encoding="utf-8")
    assert "NewClaim" in l1
    assert "OldClaim" not in l1  # superseded dropped from the default index


def test_search_warns_when_dictionary_stale(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(_ONE))
    save_state(
        tmp_path,
        DictionaryState().with_failure("simulated", "2026-07-18T10:00:00"),
    )
    out = handle_search(
        "q", 5, False, MemoryStore(tmp_path), tmp_path / "dictionary-L0.md",
        {"trowel_session_id": "", "cc_session_id": ""},
        retriever=lambda *a, **k: ["Alpha"],
    )
    assert "warning" in out
    assert "stale" in out["warning"]


def test_search_no_warning_when_consistent(tmp_path: Path) -> None:
    _note(tmp_path, "Alpha")
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(_ONE))
    out = handle_search(
        "q", 5, False, MemoryStore(tmp_path), tmp_path / "dictionary-L0.md",
        {"trowel_session_id": "", "cc_session_id": ""},
        retriever=lambda *a, **k: ["Alpha"],
    )
    assert "warning" not in out


def test_mark_stale_if_drifted_without_provider(tmp_path: Path) -> None:
    """slice-064 F6: no provider → read-only check + mark stale on drift, so
    the drift is observable (search warning) and retried when a provider returns."""
    from trowel_py.memory.dictionary import mark_dictionary_stale_if_drifted
    from trowel_py.memory.dictionary_state import load_state

    _note(tmp_path, "Alpha")
    rebuild_dictionary(tmp_path, apply=True, provider=_FakeProvider(_ONE))
    _note(tmp_path, "Beta")  # drift: Beta not indexed
    out = mark_dictionary_stale_if_drifted(tmp_path)
    assert out["dictionary_status"] == "stale"
    assert load_state(tmp_path).status == "stale"
