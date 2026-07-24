from __future__ import annotations

from trowel_py.memory.dictionary_check import (
    _evaluate,
    compute_rendered_hash,
    compute_source_hash,
)
from trowel_py.memory.types import Note


def _note(
    title: str,
    summary: str,
    *,
    tags: tuple[str, ...] = (),
) -> Note:
    return Note(
        type="note",
        title=title,
        summary=summary,
        tags=tags,
        status="active",
    )


def _fixtures() -> tuple[
    list[tuple[str, Note]],
    str,
    dict[str, str],
]:
    corpus = [
        ("b", _note("B", "other")),
        ("a", _note("A", "summary", tags=("z", "a"))),
    ]
    l0_text = "# dictionary L0\n\n### dom（2 条 → read dictionary-L1/dom.md）\n"
    l1_files = {
        "dom": (
            "# dom\n\n"
            "- **A** → `notes/a.md`：summary <!-- @stem a -->\n"
            "- **B** → `notes/b.md`：other <!-- @stem b -->\n"
        )
    }
    return corpus, l0_text, l1_files


def test_hashes_keep_exact_algorithms() -> None:
    corpus, l0_text, l1_files = _fixtures()
    assert compute_source_hash(corpus) == "ac30c4737f840a21"
    assert compute_source_hash(list(reversed(corpus))) == "ac30c4737f840a21"
    assert compute_rendered_hash(l0_text, l1_files) == "8c37d7cdc1ac9562"


def test_evaluate_returns_complete_consistent_report() -> None:
    corpus, l0_text, l1_files = _fixtures()
    report = _evaluate(
        corpus,
        l0_text,
        l1_files,
        compute_source_hash(corpus),
        state_rendered_hash=compute_rendered_hash(l0_text, l1_files),
    )
    assert report == {
        "status": "consistent",
        "active_notes": 2,
        "indexed_unique": 2,
        "missing_active": [],
        "inactive_indexed": [],
        "duplicate_entries": [],
        "missing_l1_files": [],
        "orphan_l1_files": [],
        "l0_count_mismatches": [],
        "source_hash_matches": True,
        "rendered_hash_matches": True,
    }


def test_evaluate_missing_l0_keeps_complete_report() -> None:
    corpus, _l0_text, _l1_files = _fixtures()
    report = _evaluate(
        corpus,
        None,
        {"ghost": "<!-- @stem a -->\n<!-- @stem retired -->"},
        "ignored",
        state_status="stale",
        state_rendered_hash="ignored",
    )
    assert report == {
        "status": "missing",
        "active_notes": 2,
        "indexed_unique": 2,
        "missing_active": ["a", "b"],
        "inactive_indexed": ["retired"],
        "duplicate_entries": [],
        "missing_l1_files": [],
        "orphan_l1_files": ["ghost"],
        "l0_count_mismatches": [],
        "source_hash_matches": None,
        "rendered_hash_matches": None,
    }


def test_evaluate_reports_combined_drift_with_stable_sorting() -> None:
    corpus, _l0_text, _l1_files = _fixtures()
    l0_text = "### dom（2 条\n### absent（1 条\n"
    l1_files = {
        "ghost": "<!-- @stem a -->",
        "dom": ("<!-- @stem retired -->\n<!-- @stem a -->\n<!-- @stem a -->"),
    }
    report = _evaluate(
        corpus,
        l0_text,
        l1_files,
        compute_source_hash(corpus),
    )
    assert report == {
        "status": "stale",
        "active_notes": 2,
        "indexed_unique": 2,
        "missing_active": ["b"],
        "inactive_indexed": ["retired"],
        "duplicate_entries": [
            {
                "stem": "a",
                "count": 3,
                "domains": ["dom", "ghost"],
            }
        ],
        "missing_l1_files": ["absent"],
        "orphan_l1_files": ["ghost"],
        "l0_count_mismatches": [
            {"domain": "dom", "declared": 2, "actual": 3},
            {"domain": "absent", "declared": 1, "actual": 0},
        ],
        "source_hash_matches": True,
        "rendered_hash_matches": True,
    }


def test_staging_ignores_hash_mismatch_but_not_untrusted_state() -> None:
    corpus, l0_text, l1_files = _fixtures()
    report = _evaluate(
        corpus,
        l0_text,
        l1_files,
        "different",
        state_rendered_hash="different",
        baseline_required=False,
    )
    assert report["source_hash_matches"] is False
    assert report["rendered_hash_matches"] is False
    assert report["status"] == "consistent"

    untrusted = _evaluate(
        corpus,
        l0_text,
        l1_files,
        "different",
        state_status="stale",
        state_rendered_hash="different",
        baseline_required=False,
    )
    assert untrusted["status"] == "stale"
