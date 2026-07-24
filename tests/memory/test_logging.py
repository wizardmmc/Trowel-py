from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from trowel_py.memory import logging
from trowel_py.memory.logging import OutcomeRecord


def test_access_log_appends_in_order(tmp_path: Path) -> None:
    logging.log_note_access(tmp_path, "browser-cache", "t1", "sess-A")
    logging.log_note_access(tmp_path, "build-cache", "t2", "sess-A")
    recs = logging.read_access_log(tmp_path)
    assert [r.note_id for r in recs] == ["browser-cache", "build-cache"]
    assert recs[0].context_ref == "sess-A"


def test_outcome_log_appends(tmp_path: Path) -> None:
    logging.log_session_outcome(
        tmp_path,
        "sess-A",
        "t1",
        retry_count=3,
        corrections=1,
        transcript_ref="/path/x.jsonl",
    )
    [rec] = logging.read_outcome_log(tmp_path)
    assert rec.session_ref == "sess-A"
    assert rec.retry_count == 3 and rec.corrections == 1


def test_outcome_record_has_no_classification_field() -> None:
    names = {f.name for f in fields(OutcomeRecord)}
    forbidden = {"is_miss", "label", "classification", "verdict", "kind", "miss"}
    assert not (names & forbidden), (
        f"outcome record leaked a classification field: {names & forbidden}"
    )
    assert names == {
        "session_ref",
        "when",
        "retry_count",
        "corrections",
        "transcript_ref",
    }


def test_outcome_serialized_line_has_no_label_keys(tmp_path: Path) -> None:
    logging.log_session_outcome(tmp_path, "sess-A", "t1", retry_count=2)
    line = (tmp_path / "meta" / "outcome-log.jsonl").read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    forbidden = {"is_miss", "label", "classification", "verdict", "miss"}
    assert not (set(obj) & forbidden)


def test_append_only_preserves_existing(tmp_path: Path) -> None:
    logging.log_note_access(tmp_path, "a", "t1")
    logging.log_note_access(tmp_path, "b", "t2")
    logging.log_note_access(tmp_path, "c", "t3")
    assert [r.note_id for r in logging.read_access_log(tmp_path)] == ["a", "b", "c"]


def test_read_when_absent_is_empty(tmp_path: Path) -> None:
    assert logging.read_access_log(tmp_path) == []
    assert logging.read_outcome_log(tmp_path) == []


def test_corrupt_line_skipped_not_crashed(tmp_path: Path, caplog) -> None:
    import logging as stdlog

    logging.log_note_access(tmp_path, "good-1", "t1")
    (tmp_path / "meta" / "access-log.jsonl").open("a", encoding="utf-8").write(
        "{not valid json\n"
    )
    logging.log_note_access(tmp_path, "good-2", "t2")
    with caplog.at_level(stdlog.WARNING):
        recs = logging.read_access_log(tmp_path)
    assert [r.note_id for r in recs] == ["good-1", "good-2"]
    assert any("access-log" in r.message for r in caplog.records)
