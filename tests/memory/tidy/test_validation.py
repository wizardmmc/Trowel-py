"""TidyPlan 的目标、字段与订正链校验。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.tidy import TidyOperation, apply_plan, validate_plan

from .support import make_plan, write_note


def test_validate_rejects_unknown_target(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (TidyOperation(type="retire", target="mid-ghost", reason="x"),),
        tmp_path,
    )
    errors = validate_plan(tmp_path, plan)
    assert errors
    assert any("mid-ghost" in error for error in errors)


def test_validate_rejects_supersede_cycle(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    write_note(tmp_path, "mid-b", "B")
    plan = make_plan(
        (
            TidyOperation(
                type="supersede",
                target="mid-a",
                by="mid-b",
                reason="b replaces a",
            ),
            TidyOperation(
                type="supersede",
                target="mid-b",
                by="mid-a",
                reason="a replaces b",
            ),
        ),
        tmp_path,
    )
    errors = validate_plan(tmp_path, plan)
    assert any("cycle" in error.lower() or "环" in error for error in errors)


def test_validate_rejects_merge_canonical_missing(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (
            TidyOperation(
                type="merge_sources",
                target="mid-a",
                canonical="mid-ghost",
                reason="merge",
            ),
        ),
        tmp_path,
    )
    errors = validate_plan(tmp_path, plan)
    assert any("mid-ghost" in error for error in errors)


def test_validate_accepts_clean_plan(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    write_note(tmp_path, "mid-b", "B")
    plan = make_plan(
        (
            TidyOperation(
                type="supersede",
                target="mid-a",
                by="mid-b",
                reason="b replaces a",
            ),
        ),
        tmp_path,
    )
    assert validate_plan(tmp_path, plan) == []


def test_validate_rejects_cycle_with_existing_chain(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    write_note(tmp_path, "mid-b", "B")
    first = make_plan(
        (
            TidyOperation(
                type="supersede",
                target="mid-a",
                by="mid-b",
                reason="b replaces a",
            ),
        ),
        tmp_path,
    )
    apply_plan(tmp_path, first)
    second = make_plan(
        (
            TidyOperation(
                type="supersede",
                target="mid-b",
                by="mid-a",
                reason="a replaces b",
            ),
        ),
        tmp_path,
    )
    errors = validate_plan(tmp_path, second)
    assert any("cycle" in error.lower() or "环" in error for error in errors)


def test_validate_rejects_self_supersede(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (
            TidyOperation(
                type="supersede",
                target="mid-a",
                by="mid-a",
                reason="self",
            ),
        ),
        tmp_path,
    )
    errors = validate_plan(tmp_path, plan)
    assert any("self" in error.lower() or "自指" in error for error in errors)


def test_validate_rejects_revise_identity_field(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (
            TidyOperation(
                type="revise",
                target="mid-a",
                reason="malicious",
                new_fields={"memory_id": "BROKEN"},
            ),
        ),
        tmp_path,
    )
    errors = validate_plan(tmp_path, plan)
    assert any("allowlist" in error.lower() or "memory_id" in error for error in errors)


def test_validate_rejects_revise_status(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (
            TidyOperation(
                type="revise",
                target="mid-a",
                reason="lifecycle",
                new_fields={"status": "retired"},
            ),
        ),
        tmp_path,
    )
    assert validate_plan(tmp_path, plan)


def test_validate_rejects_revise_bad_enum(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (
            TidyOperation(
                type="revise",
                target="mid-a",
                reason="bad enum",
                new_fields={"verification": "not-a-real-value"},
            ),
        ),
        tmp_path,
    )
    errors = validate_plan(tmp_path, plan)
    assert any("schema" in error.lower() or "verification" in error for error in errors)


def test_validate_accepts_revise_allowlisted(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (
            TidyOperation(
                type="revise",
                target="mid-a",
                reason="scope",
                new_fields={"trigger": "when X", "verification": "verified"},
            ),
        ),
        tmp_path,
    )
    assert validate_plan(tmp_path, plan) == []
