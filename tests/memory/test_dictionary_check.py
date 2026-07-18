"""dictionary consistency check (slice-064 §2): read-only report.

Hand-built minimal indexes cover missing/stale/duplicate/count mismatch/
orphan/source-hash mismatch. The check never calls an LLM and never writes.
"""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.dictionary_check import (
    check_dictionary,
    compute_rendered_hash,
    compute_source_hash,
    derive_active_corpus,
)
from trowel_py.memory.dictionary_state import save_state, DictionaryState
from trowel_py.memory.store import MemoryStore


def _note(root: Path, title: str, summary: str = "s", tags=None, status="active") -> str:
    store = MemoryStore(root)
    stem = store.write_note({
        "type": "note", "title": title, "summary": summary,
        "tags": tags or [], "kind": "fact", "verification": "verified",
        "refs": 0, "last_ref": "",
    })
    if status != "active":
        store.update_note_fields(stem, {"status": status})
    return stem


def _l1(root: Path, domain: str, entries: list[tuple[str, str, str]]) -> None:
    """entries: [(stem, title, summary)]. Writes one L1 file in render format
    (with the @stem anchor, matching ``_render_l1``)."""
    d = root / "dictionary-L1"
    d.mkdir(parents=True, exist_ok=True)
    lines = [f"# {domain}", ""]
    for stem, title, summary in entries:
        lines.append(
            f"- **{title}** → `notes/{stem}.md`：{summary}｜触发词：{title}"
            f" <!-- @stem {stem} -->"
        )
    (d / f"{domain}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _l0(root: Path, domains: list[tuple[str, int]]) -> None:
    """domains: [(name, declared_count)]."""
    lines = ["# dictionary L0", ""]
    for name, count in domains:
        lines.append(f"### {name}（{count} 条 → read dictionary-L1/{name}.md）")
        lines.append("")
    (root / "dictionary-L0.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stamp_consistent(root: Path) -> None:
    """Write a state whose source_hash + rendered_hash match the on-disk index."""
    corpus = derive_active_corpus(root)
    l0_path = root / "dictionary-L0.md"
    l1_dir = root / "dictionary-L1"
    l0_text = l0_path.read_text(encoding="utf-8") if l0_path.exists() else ""
    l1_files = (
        {p.stem: p.read_text(encoding="utf-8") for p in l1_dir.glob("*.md")}
        if l1_dir.exists() else {}
    )
    save_state(
        root,
        DictionaryState().with_success(
            compute_source_hash(corpus),
            compute_rendered_hash(l0_text, l1_files),
            "2026-07-18T10:00:00",
        ),
    )


def test_derive_active_corpus_excludes_inactive(tmp_path: Path) -> None:
    a = _note(tmp_path, "Active One")
    _note(tmp_path, "Dead One", status="superseded")
    _note(tmp_path, "Retired One", status="retired")
    _note(tmp_path, "Contradicted One", status="contradicted")
    corpus = derive_active_corpus(tmp_path)
    stems = {s for s, _ in corpus}
    assert stems == {a}


def test_source_hash_order_invariant(tmp_path: Path) -> None:
    _note(tmp_path, "A", "aa")
    _note(tmp_path, "B", "bb")
    h1 = compute_source_hash(derive_active_corpus(tmp_path))
    # re-derive in a different order — hash must not depend on file/iter order
    h2 = compute_source_hash(sorted(derive_active_corpus(tmp_path), reverse=True))
    assert h1 == h2
    # changing a field a searcher sees must change the hash
    _note(tmp_path, "C", "cc")
    h3 = compute_source_hash(derive_active_corpus(tmp_path))
    assert h3 != h1


def test_check_missing_when_no_dictionary(tmp_path: Path) -> None:
    _note(tmp_path, "A")
    report = check_dictionary(tmp_path)
    assert report["status"] == "missing"
    assert report["active_notes"] == 1
    assert report["indexed_unique"] == 0


def test_check_consistent(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha", "a note")
    b = _note(tmp_path, "Beta", "b note")
    _l1(tmp_path, "dom1", [(a, "Alpha", "a note"), (b, "Beta", "b note")])
    _l0(tmp_path, [("dom1", 2)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "consistent", report
    assert report["active_notes"] == 2
    assert report["indexed_unique"] == 2
    assert report["missing_active"] == []
    assert report["inactive_indexed"] == []
    assert report["duplicate_entries"] == []
    assert report["missing_l1_files"] == []
    assert report["orphan_l1_files"] == []
    assert report["l0_count_mismatches"] == []
    assert report["source_hash_matches"] is True


def test_check_missing_active(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha")
    _note(tmp_path, "Beta")  # not indexed anywhere
    _l1(tmp_path, "dom1", [(a, "Alpha", "s")])
    _l0(tmp_path, [("dom1", 1)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    # Beta is the stem; its title was slugified — missing_active carries stems
    assert len(report["missing_active"]) == 1


def test_check_inactive_indexed(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha")
    dead = _note(tmp_path, "Dead", status="superseded")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s"), (dead, "Dead", "s")])
    _l0(tmp_path, [("dom1", 2)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    assert dead in report["inactive_indexed"]


def test_check_duplicate_entry(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s")])
    _l1(tmp_path, "dom2", [(a, "Alpha", "s")])  # same stem in a 2nd domain
    _l0(tmp_path, [("dom1", 1), ("dom2", 1)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    dupes = {d["stem"] for d in report["duplicate_entries"]}
    assert a in dupes


def test_check_missing_l1_file(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s")])
    # L0 advertises dom1 + dom2, but dom2.md was never written
    _l0(tmp_path, [("dom1", 1), ("dom2", 0)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    assert "dom2" in report["missing_l1_files"]


def test_check_orphan_l1_file(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s")])
    _l1(tmp_path, "ghost", [(a, "Alpha", "s")])  # not declared in L0
    _l0(tmp_path, [("dom1", 1)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    assert "ghost" in report["orphan_l1_files"]


def test_check_l0_count_mismatch(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha")
    b = _note(tmp_path, "Beta")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s"), (b, "Beta", "s")])  # actual 2
    _l0(tmp_path, [("dom1", 9)])  # L0 claims 9
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    mm = {m["domain"]: (m["declared"], m["actual"]) for m in report["l0_count_mismatches"]}
    assert mm == {"dom1": (9, 2)}


def test_check_source_hash_mismatch(tmp_path: Path) -> None:
    a = _note(tmp_path, "Alpha")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s")])
    _l0(tmp_path, [("dom1", 1)])
    # state claims a source_hash the current corpus does NOT produce
    l0_text = (tmp_path / "dictionary-L0.md").read_text(encoding="utf-8")
    l1_files = {
        p.stem: p.read_text(encoding="utf-8")
        for p in (tmp_path / "dictionary-L1").glob("*.md")
    }
    save_state(
        tmp_path,
        DictionaryState().with_success(
            "stale-hash", compute_rendered_hash(l0_text, l1_files),
            "2026-07-18T10:00:00",
        ),
    )
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    assert report["source_hash_matches"] is False


def test_check_no_state_flags_unverified(tmp_path: Path) -> None:
    # no state baseline → the hash cannot be confirmed. This must NOT read as
    # consistent: a title/summary/tags drift (stems + counts unchanged) would
    # otherwise be masked. The index is unverified until a rebuild stamps a hash.
    a = _note(tmp_path, "Alpha")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s")])
    _l0(tmp_path, [("dom1", 1)])
    report = check_dictionary(tmp_path)
    assert report["source_hash_matches"] is False
    assert report["status"] == "stale"


def test_check_anchor_round_trips_backtick_in_stem(tmp_path: Path) -> None:
    """slice-064: the @stem anchor round-trips a stem that contains a backtick
    (which would break the legacy `` `notes/{stem}.md` `` code span)."""
    store = MemoryStore(tmp_path)
    stem = store.write_note({
        "type": "note", "title": "a`b", "summary": "s", "tags": [],
        "kind": "fact", "verification": "verified", "refs": 0, "last_ref": "",
    })
    assert "`" in stem  # slug keeps the backtick; the anchor must still cope
    _l1(tmp_path, "dom1", [(stem, "a`b", "s")])
    _l0(tmp_path, [("dom1", 1)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "consistent", report
    assert report["missing_active"] == []


def test_check_legacy_l1_without_anchor_uses_backtick(tmp_path: Path) -> None:
    """Pre-anchor L1 files (no ``<!-- @stem -->``) still parse via the legacy
    backtick extractor, so the check tolerates indexes rendered before 064."""
    a = _note(tmp_path, "Alpha")
    l1d = tmp_path / "dictionary-L1"
    l1d.mkdir(parents=True)
    (l1d / "dom1.md").write_text(
        f"# dom1\n\n- **Alpha** → `notes/{a}.md`：s｜触发词：Alpha\n",
        encoding="utf-8",
    )
    _l0(tmp_path, [("dom1", 1)])
    _stamp_consistent(tmp_path)
    report = check_dictionary(tmp_path)
    assert report["status"] == "consistent", report


def test_check_stale_state_overrides_matching_hash(tmp_path: Path) -> None:
    """state.status=stale must stay stale even when structure + hash match
    (slice-064 C-7/F3): a failed/unfinished rebuild stays observable + retryable
    instead of being silently re-certified consistent."""
    a = _note(tmp_path, "Alpha")
    _l1(tmp_path, "dom1", [(a, "Alpha", "s")])
    _l0(tmp_path, [("dom1", 1)])
    corpus_hash = compute_source_hash(derive_active_corpus(tmp_path))
    l0_text = (tmp_path / "dictionary-L0.md").read_text(encoding="utf-8")
    l1_files = {
        p.stem: p.read_text(encoding="utf-8")
        for p in (tmp_path / "dictionary-L1").glob("*.md")
    }
    rendered = compute_rendered_hash(l0_text, l1_files)
    # stale state but carrying hashes that MATCH the current corpus (as if a
    # rebuild failed right after the index was already correct)
    save_state(
        tmp_path,
        DictionaryState()
        .with_success(corpus_hash, rendered, "2026-07-18T10:00:00")
        .with_failure("provider 529", "2026-07-18T11:00:00"),
    )
    report = check_dictionary(tmp_path)
    assert report["source_hash_matches"] is True  # the hash itself matches
    assert report["status"] == "stale"  # but state stale → not consistent


def test_check_hand_edited_l1_content_is_detected(tmp_path: Path) -> None:
    """slice-064 F4: editing an L1 entry's rendered text (stems + counts
    unchanged, notes unchanged) is caught via rendered_hash — source_hash
    alone would mask it."""
    a = _note(tmp_path, "Alpha", "original summary")
    _l1(tmp_path, "dom1", [(a, "Alpha", "original summary")])
    _l0(tmp_path, [("dom1", 1)])
    _stamp_consistent(tmp_path)
    # tamper with the rendered content only (stem + count untouched)
    l1_file = tmp_path / "dictionary-L1" / "dom1.md"
    l1_file.write_text(
        l1_file.read_text(encoding="utf-8").replace("original summary", "tampered"),
        encoding="utf-8",
    )
    report = check_dictionary(tmp_path)
    assert report["status"] == "stale"
    assert report["rendered_hash_matches"] is False
    assert report["source_hash_matches"] is True  # notes did not change


def test_retriever_parse_l1_stems_prefers_anchor() -> None:
    """slice-064 F5: the retriever's stem extractor uses the @stem anchor, so a
    stem containing a backtick (which breaks the `notes/{stem}.md` code span) is
    still retrievable by search — not just by the check."""
    from trowel_py.memory.retrievers import _parse_l1_stems

    text = "- **a`b** → `notes/a`b.md`：s <!-- @stem a`b -->"
    assert _parse_l1_stems(text) == {"a`b"}


def test_check_missing_l0_reports_orphans_and_missing(tmp_path: Path) -> None:
    """slice-064 F12: with no L0 but stray L1 files, the report still enumerates
    the orphan L1 files and reports every active note as missing (the L0 is the
    retriever's entry point, so nothing is discoverable without it)."""
    a = _note(tmp_path, "Alpha")
    _note(tmp_path, "Beta")  # in no L1 at all
    _l1(tmp_path, "ghost", [(a, "Alpha", "s")])  # L1 file with no L0 to declare it
    report = check_dictionary(tmp_path)
    assert report["status"] == "missing"
    assert report["orphan_l1_files"] == ["ghost"]
    assert set(report["missing_active"]) == {a, "Beta"}  # Beta slug
    assert report["indexed_unique"] == 1
