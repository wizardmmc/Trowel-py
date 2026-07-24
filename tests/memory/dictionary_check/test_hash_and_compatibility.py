from pathlib import Path

from trowel_py.memory.dictionary_check import (
    check_dictionary,
    compute_rendered_hash,
    compute_source_hash,
    derive_active_corpus,
)
from trowel_py.memory.dictionary_state import DictionaryState, save_state
from trowel_py.memory.store import MemoryStore

from .support import (
    read_l1_files,
    stamp_consistent,
    write_l0,
    write_l1,
    write_note,
)


def test_active_corpus_excludes_inactive_notes(tmp_path: Path) -> None:
    active = write_note(tmp_path, "Active One")
    write_note(tmp_path, "Dead One", status="superseded")
    write_note(tmp_path, "Retired One", status="retired")
    write_note(tmp_path, "Contradicted One", status="contradicted")

    corpus = derive_active_corpus(tmp_path)

    assert {stem for stem, _note in corpus} == {active}


def test_source_hash_is_order_invariant_and_content_sensitive(
    tmp_path: Path,
) -> None:
    write_note(tmp_path, "A", "aa")
    write_note(tmp_path, "B", "bb")
    corpus = derive_active_corpus(tmp_path)

    first_hash = compute_source_hash(corpus)
    reordered_hash = compute_source_hash(sorted(corpus, reverse=True))
    write_note(tmp_path, "C", "cc")
    changed_hash = compute_source_hash(derive_active_corpus(tmp_path))

    assert reordered_hash == first_hash
    assert changed_hash != first_hash


def test_source_hash_mismatch_marks_stale(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_l1(tmp_path, "dom1", [(alpha, "Alpha", "s")])
    write_l0(tmp_path, [("dom1", 1)])
    l0_text = (tmp_path / "dictionary-L0.md").read_text(encoding="utf-8")
    save_state(
        tmp_path,
        DictionaryState().with_success(
            "stale-hash",
            compute_rendered_hash(l0_text, read_l1_files(tmp_path)),
            "2026-07-18T10:00:00",
        ),
    )

    report = check_dictionary(tmp_path)

    assert report["status"] == "stale"
    assert report["source_hash_matches"] is False


def test_missing_state_keeps_matching_structure_unverified(
    tmp_path: Path,
) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_l1(tmp_path, "dom1", [(alpha, "Alpha", "s")])
    write_l0(tmp_path, [("dom1", 1)])

    report = check_dictionary(tmp_path)

    assert report["source_hash_matches"] is False
    assert report["status"] == "stale"


def test_stem_anchor_round_trips_backtick(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    stem = store.write_note(
        {
            "type": "note",
            "title": "a`b",
            "summary": "s",
            "tags": [],
            "kind": "fact",
            "verification": "verified",
            "refs": 0,
            "last_ref": "",
        }
    )
    assert "`" in stem
    write_l1(tmp_path, "dom1", [(stem, "a`b", "s")])
    write_l0(tmp_path, [("dom1", 1)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)

    assert report["status"] == "consistent", report
    assert report["missing_active"] == []


def test_legacy_l1_without_anchor_uses_code_span(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    l1_directory = tmp_path / "dictionary-L1"
    l1_directory.mkdir(parents=True)
    (l1_directory / "dom1.md").write_text(
        f"# dom1\n\n- **Alpha** → `notes/{alpha}.md`：s｜触发词：Alpha\n",
        encoding="utf-8",
    )
    write_l0(tmp_path, [("dom1", 1)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)

    assert report["status"] == "consistent", report


def test_stale_state_overrides_matching_hash(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_l1(tmp_path, "dom1", [(alpha, "Alpha", "s")])
    write_l0(tmp_path, [("dom1", 1)])
    corpus_hash = compute_source_hash(derive_active_corpus(tmp_path))
    l0_text = (tmp_path / "dictionary-L0.md").read_text(encoding="utf-8")
    rendered_hash = compute_rendered_hash(
        l0_text,
        read_l1_files(tmp_path),
    )
    save_state(
        tmp_path,
        DictionaryState()
        .with_success(
            corpus_hash,
            rendered_hash,
            "2026-07-18T10:00:00",
        )
        .with_failure(
            "provider 529",
            "2026-07-18T11:00:00",
        ),
    )

    report = check_dictionary(tmp_path)

    assert report["source_hash_matches"] is True
    assert report["status"] == "stale"


def test_hand_edited_l1_content_is_detected(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha", "original summary")
    write_l1(
        tmp_path,
        "dom1",
        [(alpha, "Alpha", "original summary")],
    )
    write_l0(tmp_path, [("dom1", 1)])
    stamp_consistent(tmp_path)
    l1_file = tmp_path / "dictionary-L1" / "dom1.md"
    l1_file.write_text(
        l1_file.read_text(encoding="utf-8").replace(
            "original summary",
            "tampered",
        ),
        encoding="utf-8",
    )

    report = check_dictionary(tmp_path)

    assert report["status"] == "stale"
    assert report["rendered_hash_matches"] is False
    assert report["source_hash_matches"] is True
