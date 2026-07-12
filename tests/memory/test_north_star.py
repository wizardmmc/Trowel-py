"""slice-041 north-star metrics tests."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.access_log import AccessRecord, OutcomeRecord, log_access, log_outcome
from trowel_py.memory.north_star import compute_north_star
from trowel_py.memory.store import MemoryStore


def _note(root: Path, mid: str, *, status: str = "active", harmful_refs: int = 0) -> str:
    return MemoryStore(root).write_note({
        "type": "note", "title": f"n-{mid}", "verification": "verified",
        "memory_id": mid, "status": status, "harmful_refs": harmful_refs,
        "__body": "x",
    })


def test_harmful_rate_zero_when_all_active_clean(tmp_path: Path) -> None:
    _note(tmp_path, "a", status="active")
    _note(tmp_path, "b", status="active")
    m = compute_north_star(tmp_path, today="2026-07-11")
    assert m["harmful_memory_rate"] == 0.0
    assert m["active_notes"] == 2
    assert m["known_issue_repeat_rate"] is None  # TODO


def test_harmful_rate_counts_contradicted(tmp_path: Path) -> None:
    _note(tmp_path, "a", status="active")
    _note(tmp_path, "b", status="active")
    _note(tmp_path, "c", status="contradicted")
    _note(tmp_path, "d", status="superseded")
    m = compute_north_star(tmp_path, today="2026-07-11")
    # W6: 2 flagged / 4 non-retired = 0.5 (same population, not 2/2 active = 1.0)
    assert m["contradicted_or_superseded"] == 2
    assert m["active_notes"] == 2
    assert m["harmful_memory_rate"] == 0.5


def test_harmful_rate_never_exceeds_one_when_corrections_accumulate(tmp_path: Path) -> None:
    """W6 (codex): corrections accumulate over time; the rate must stay <=1.0
    because numerator and denominator share the same non-retired population."""
    # 2 active, 5 contradicted (historical corrections piled up)
    for i in range(2):
        _note(tmp_path, f"a{i}", status="active")
    for i in range(5):
        _note(tmp_path, f"c{i}", status="contradicted")
    m = compute_north_star(tmp_path, today="2026-07-11")
    # 5 contradicted / 7 non-retired ~= 0.7143 (NOT 5/2 = 2.5)
    assert m["harmful_memory_rate"] <= 1.0
    assert m["harmful_memory_rate"] == round(5 / 7, 4)


def test_harmful_rate_counts_high_harmful_refs(tmp_path: Path) -> None:
    _note(tmp_path, "a", status="active", harmful_refs=5)  # ≥ threshold (3)
    _note(tmp_path, "b", status="active", harmful_refs=1)  # below
    m = compute_north_star(tmp_path, today="2026-07-11")
    # 1 harmful_high / 2 active = 0.5
    assert m["harmful_high_notes"] == 1
    assert m["harmful_memory_rate"] == 0.5


def test_metrics_carry_raw_log_material(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    log_access(tmp_path, AccessRecord(
        ts="2026-07-01T10:00:00+00:00", trowel_session_id="t", cc_session_id="c",
        toolUseId="tu", action="read", search_id="s", read_id="r", memory_id="a",
    ))
    log_outcome(tmp_path, OutcomeRecord(
        ts="2026-07-01T10:01:00+00:00", trowel_session_id="t", cc_session_id="c",
        toolUseId="tu", read_id="r", memory_id="a", outcome="harmful",
    ))
    m = compute_north_star(tmp_path, today="2026-07-11")
    assert m["raw_reads"] == 1
    assert m["raw_harmful_outcomes"] == 1


def test_no_notes_does_not_divide_by_zero(tmp_path: Path) -> None:
    m = compute_north_star(tmp_path, today="2026-07-11")
    assert m["harmful_memory_rate"] == 0.0
    assert m["active_notes"] == 0
