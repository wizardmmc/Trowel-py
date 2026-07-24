from __future__ import annotations

import inspect
from pathlib import Path

import trowel_py.memory.north_star as north_star_module
from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
)
from trowel_py.memory.north_star import compute_north_star

from .support import write_note


def test_harmful_rate_zero_when_all_active_clean(tmp_path: Path) -> None:
    write_note(tmp_path, "a", status="active")
    write_note(tmp_path, "b", status="active")

    metrics = compute_north_star(tmp_path, today="2026-07-11")

    assert metrics["harmful_memory_rate"] == 0.0
    assert metrics["active_notes"] == 2
    assert metrics["known_issue_repeat_rate"] is None


def test_harmful_rate_counts_contradicted(tmp_path: Path) -> None:
    write_note(tmp_path, "a", status="active")
    write_note(tmp_path, "b", status="active")
    write_note(tmp_path, "c", status="contradicted")
    write_note(tmp_path, "d", status="superseded")

    metrics = compute_north_star(tmp_path, today="2026-07-11")

    assert metrics["contradicted_or_superseded"] == 2
    assert metrics["active_notes"] == 2
    assert metrics["harmful_memory_rate"] == 0.5


def test_harmful_rate_never_exceeds_one_when_corrections_accumulate(
    tmp_path: Path,
) -> None:
    for index in range(2):
        write_note(tmp_path, f"a{index}", status="active")
    for index in range(5):
        write_note(tmp_path, f"c{index}", status="contradicted")

    metrics = compute_north_star(tmp_path, today="2026-07-11")

    assert metrics["harmful_memory_rate"] <= 1.0
    assert metrics["harmful_memory_rate"] == round(5 / 7, 4)


def test_harmful_rate_counts_high_harmful_refs(tmp_path: Path) -> None:
    write_note(tmp_path, "a", status="active", harmful_refs=5)
    write_note(tmp_path, "b", status="active", harmful_refs=1)

    metrics = compute_north_star(tmp_path, today="2026-07-11")

    assert metrics["harmful_high_notes"] == 1
    assert metrics["harmful_memory_rate"] == 0.5


def test_metrics_carry_raw_log_material(tmp_path: Path) -> None:
    write_note(tmp_path, "a")
    log_access(
        tmp_path,
        AccessRecord(
            ts="2026-07-01T10:00:00+00:00",
            trowel_session_id="t",
            cc_session_id="c",
            toolUseId="tu",
            action="read",
            search_id="s",
            read_id="r",
            memory_id="a",
        ),
    )
    log_outcome(
        tmp_path,
        OutcomeRecord(
            ts="2026-07-01T10:01:00+00:00",
            trowel_session_id="t",
            cc_session_id="c",
            toolUseId="tu",
            read_id="r",
            memory_id="a",
            outcome="harmful",
        ),
    )

    metrics = compute_north_star(tmp_path, today="2026-07-11")

    assert metrics["raw_reads"] == 1
    assert metrics["raw_harmful_outcomes"] == 1


def test_no_notes_does_not_divide_by_zero(tmp_path: Path) -> None:
    metrics = compute_north_star(tmp_path, today="2026-07-11")

    assert metrics["harmful_memory_rate"] == 0.0
    assert metrics["active_notes"] == 0


def test_facade_preserves_function_identity_and_patch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class EmptyStore:
        def __init__(self, root: Path | str) -> None:
            pass

        def load_notes_with_id(self) -> list:
            return []

    monkeypatch.setattr(north_star_module, "MemoryStore", EmptyStore)
    monkeypatch.setattr(north_star_module, "HARMFUL_RETIRE_THRESHOLD", 9)

    metrics = compute_north_star(tmp_path, today="2026-07-11")

    assert compute_north_star.__module__ == "trowel_py.memory.north_star"
    assert str(inspect.signature(compute_north_star)) == (
        "(root: 'Path | str', *, today: 'str | None' = None) -> 'dict[str, Any]'"
    )
    assert metrics["harmful_threshold"] == 9
