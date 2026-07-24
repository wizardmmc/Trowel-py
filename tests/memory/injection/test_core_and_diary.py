from __future__ import annotations

import logging
from pathlib import Path

import pytest

from trowel_py.memory.injection import _render_diary, build_memory_injection
from trowel_py.memory.store import MemoryStore

from .support import item, write_core, write_diary, write_l0


def test_injects_active_and_seed_core(tmp_path: Path) -> None:
    write_core(
        tmp_path,
        [
            item("a", "ACTIVE_MARKER imperative", "active"),
            item("s", "SEED_MARKER imperative", "seed"),
        ],
    )

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "ACTIVE_MARKER" in output
    assert "SEED_MARKER" in output


def test_filters_retired_core(tmp_path: Path) -> None:
    write_core(
        tmp_path,
        [
            item("a", "ACTIVE_MARKER imperative", "active"),
            item("r", "RETIRED_MARKER imperative", "retired"),
        ],
    )

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "ACTIVE_MARKER" in output
    assert "RETIRED_MARKER" not in output


def test_injects_l0(tmp_path: Path) -> None:
    write_core(tmp_path, [item("a", "x", "active")])
    write_l0(tmp_path, "L0_DOMAIN_INDEX_MARKER")

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "L0_DOMAIN_INDEX_MARKER" in output


def test_injects_recent_daily_diary(tmp_path: Path) -> None:
    write_core(tmp_path, [item("a", "x", "active")])
    write_diary(tmp_path, "2026-07-08", "day", "RECENT_DAY_MARKER")

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "RECENT_DAY_MARKER" in output


def test_excludes_daily_beyond_7d(tmp_path: Path) -> None:
    write_core(tmp_path, [item("a", "x", "active")])
    write_diary(tmp_path, "2026-06-01", "day", "OLD_DAY_MARKER")

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "OLD_DAY_MARKER" not in output


def test_injects_recent_weekly(tmp_path: Path) -> None:
    write_core(tmp_path, [item("a", "x", "active")])
    write_diary(tmp_path, "2026-W28", "week", "WEEK_MARKER")

    output = build_memory_injection("2026-07-15", root=tmp_path)

    assert "WEEK_MARKER" in output


def test_injects_this_year_monthly_even_if_old(tmp_path: Path) -> None:
    write_core(tmp_path, [item("a", "x", "active")])
    write_diary(tmp_path, "2026-01-10", "month", "JAN_MONTH_MARKER")

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "JAN_MONTH_MARKER" in output


def test_empty_memory_returns_memory_root_only(tmp_path: Path) -> None:
    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "memory 根路径" in output
    assert "memory.search" in output
    assert "铁律" not in output


def test_injection_carries_memory_root_and_tool_usage(tmp_path: Path) -> None:
    write_core(tmp_path, [item("c1", "X", "active")])

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "memory 根路径 + 检索" in output
    assert "memory.search(query)" in output
    assert "memory.read(uri)" in output
    assert str(tmp_path.resolve()) in output
    assert "requires_read" in output


def test_no_notes_body_injected(tmp_path: Path) -> None:
    write_core(tmp_path, [item("a", "x", "active")])
    MemoryStore(tmp_path).write_note(
        {
            "type": "note",
            "title": "NOTE_SECRET_MARKER title",
            "tags": [],
            "summary": "NOTE_SECRET_MARKER summary",
            "confidence": "draft",
            "verification": "inferred-untested",
            "refs": 0,
            "last_ref": "",
            "retired": False,
            "pain": 0,
            "created": "2026-07-09",
            "updated": "2026-07-09",
        }
    )

    output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "NOTE_SECRET_MARKER" not in output


def test_token_budget_warning_on_oversize(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_core(tmp_path, [item("a", "大" * 20000, "active")])

    with caplog.at_level(logging.WARNING):
        output = build_memory_injection("2026-07-09", root=tmp_path)

    assert "大" in output
    assert any(
        "budget" in record.message.lower() or "token" in record.message.lower()
        for record in caplog.records
    )


def test_bad_now_skips_diary_not_core(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_core(tmp_path, [item("a", "CORE_MARKER", "active")])
    write_diary(tmp_path, "2026-07-05", "day", "DAY_MARKER")

    with caplog.at_level(logging.WARNING):
        output = build_memory_injection("2026-7-5", root=tmp_path)

    assert "CORE_MARKER" in output
    assert "DAY_MARKER" not in output
    assert any("malformed" in record.message.lower() for record in caplog.records)


def test_injection_caps_earlier_monthlies_to_three(tmp_path: Path) -> None:
    for month in ("2025-06", "2025-07", "2025-08", "2025-09", "2025-10"):
        write_diary(tmp_path, month, "month", f"month {month}")

    output = build_memory_injection("2026-07-11", root=tmp_path)

    assert "2025-10" in output
    assert "2025-09" in output
    assert "2025-08" in output
    assert "2025-07" not in output
    assert "2025-06" not in output


def test_render_diary_include_layers_drops_earlier(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    write_diary(tmp_path, "2026-07-09", "day", "today gotcha")
    write_diary(tmp_path, "2025-06", "month", "old month flow")

    full = _render_diary(store, "2026-07-11", include_layers=4)
    truncated = _render_diary(store, "2026-07-11", include_layers=1)

    assert "old month flow" in full
    assert "old month flow" not in truncated
    assert "today gotcha" in truncated
