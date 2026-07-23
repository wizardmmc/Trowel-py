from pathlib import Path

from trowel_py.memory.dictionary_check import check_dictionary

from .support import stamp_consistent, write_l0, write_l1, write_note


def test_missing_when_no_dictionary(tmp_path: Path) -> None:
    write_note(tmp_path, "A")

    report = check_dictionary(tmp_path)

    assert report["status"] == "missing"
    assert report["active_notes"] == 1
    assert report["indexed_unique"] == 0


def test_consistent_dictionary(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha", "a note")
    beta = write_note(tmp_path, "Beta", "b note")
    write_l1(
        tmp_path,
        "dom1",
        [(alpha, "Alpha", "a note"), (beta, "Beta", "b note")],
    )
    write_l0(tmp_path, [("dom1", 2)])
    stamp_consistent(tmp_path)

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


def test_missing_active_note_marks_stale(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_note(tmp_path, "Beta")
    write_l1(tmp_path, "dom1", [(alpha, "Alpha", "s")])
    write_l0(tmp_path, [("dom1", 1)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)

    assert report["status"] == "stale"
    assert len(report["missing_active"]) == 1


def test_inactive_indexed_note_marks_stale(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    inactive = write_note(tmp_path, "Dead", status="superseded")
    write_l1(
        tmp_path,
        "dom1",
        [(alpha, "Alpha", "s"), (inactive, "Dead", "s")],
    )
    write_l0(tmp_path, [("dom1", 2)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)

    assert report["status"] == "stale"
    assert inactive in report["inactive_indexed"]


def test_duplicate_entry_marks_stale(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_l1(tmp_path, "dom1", [(alpha, "Alpha", "s")])
    write_l1(tmp_path, "dom2", [(alpha, "Alpha", "s")])
    write_l0(tmp_path, [("dom1", 1), ("dom2", 1)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)

    assert report["status"] == "stale"
    assert alpha in {
        duplicate["stem"]
        for duplicate in report["duplicate_entries"]
    }


def test_missing_l1_file_marks_stale(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_l1(tmp_path, "dom1", [(alpha, "Alpha", "s")])
    write_l0(tmp_path, [("dom1", 1), ("dom2", 0)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)

    assert report["status"] == "stale"
    assert "dom2" in report["missing_l1_files"]


def test_orphan_l1_file_marks_stale(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_l1(tmp_path, "dom1", [(alpha, "Alpha", "s")])
    write_l1(tmp_path, "ghost", [(alpha, "Alpha", "s")])
    write_l0(tmp_path, [("dom1", 1)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)

    assert report["status"] == "stale"
    assert "ghost" in report["orphan_l1_files"]


def test_l0_count_mismatch_marks_stale(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    beta = write_note(tmp_path, "Beta")
    write_l1(
        tmp_path,
        "dom1",
        [(alpha, "Alpha", "s"), (beta, "Beta", "s")],
    )
    write_l0(tmp_path, [("dom1", 9)])
    stamp_consistent(tmp_path)

    report = check_dictionary(tmp_path)
    mismatches = {
        mismatch["domain"]: (
            mismatch["declared"],
            mismatch["actual"],
        )
        for mismatch in report["l0_count_mismatches"]
    }

    assert report["status"] == "stale"
    assert mismatches == {"dom1": (9, 2)}


def test_missing_l0_reports_orphans_and_missing_notes(tmp_path: Path) -> None:
    alpha = write_note(tmp_path, "Alpha")
    write_note(tmp_path, "Beta")
    write_l1(tmp_path, "ghost", [(alpha, "Alpha", "s")])

    report = check_dictionary(tmp_path)

    assert report["status"] == "missing"
    assert report["orphan_l1_files"] == ["ghost"]
    assert set(report["missing_active"]) == {alpha, "Beta"}
    assert report["indexed_unique"] == 1
