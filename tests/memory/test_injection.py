"""tests for memory injection assembly (slice-039 T1).

Drives the API of ``build_memory_injection`` and the store enhancements it
needs (``load_core_items`` + diary ``body``). Fixtures are hand-built memory
trees under tmp_path — no合成 cc data (the cc system-prompt shape was already
captured by the 2026-07-09 spike; these tests cover the assembly logic).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from trowel_py.memory.injection import build_memory_injection
from trowel_py.memory.store import MemoryStore


def _item(id_: str, imperative: str, status: str = "active") -> dict:
    return {
        "id": id_,
        "imperative": imperative,
        "scope": "high-risk",
        "status": status,
        "source": "test",
    }


def _write_core(root: Path, items: list[dict]) -> None:
    fm = {"type": "core", "items": items}
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    (root / "core.md").write_text(f"---\n{dumped}---\nbody\n", encoding="utf-8")


def _write_l0(root: Path, text: str) -> None:
    (root / "dictionary-L0.md").write_text(text, encoding="utf-8")


def _write_diary(root: Path, date_: str, layer: str, body: str) -> None:
    sub = {"day": "daily", "week": "weekly", "month": "monthly"}[layer]
    d = root / "diary" / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date_}.md").write_text(
        "---\n"
        f"type: diary\ndate: '{date_}'\nlayer: {layer}\n"
        f"period: '{date_}'\npromoted_knowledge: []\n"
        f"---\n{body}\n",
        encoding="utf-8",
    )


def test_injects_active_and_seed_core(tmp_path: Path) -> None:
    _write_core(tmp_path, [
        _item("a", "ACTIVE_MARKER imperative", "active"),
        _item("s", "SEED_MARKER imperative", "seed"),
    ])
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "ACTIVE_MARKER" in out
    assert "SEED_MARKER" in out


def test_filters_retired_core(tmp_path: Path) -> None:
    # C-1/C-2: retired imperatives must NOT be injected.
    _write_core(tmp_path, [
        _item("a", "ACTIVE_MARKER imperative", "active"),
        _item("r", "RETIRED_MARKER imperative", "retired"),
    ])
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "ACTIVE_MARKER" in out
    assert "RETIRED_MARKER" not in out


def test_injects_l0(tmp_path: Path) -> None:
    _write_core(tmp_path, [_item("a", "x", "active")])
    _write_l0(tmp_path, "L0_DOMAIN_INDEX_MARKER")
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "L0_DOMAIN_INDEX_MARKER" in out


def test_injects_recent_daily_diary(tmp_path: Path) -> None:
    _write_core(tmp_path, [_item("a", "x", "active")])
    _write_diary(tmp_path, "2026-07-05", "day", "RECENT_DAY_MARKER")  # within 7d
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "RECENT_DAY_MARKER" in out


def test_excludes_daily_beyond_7d(tmp_path: Path) -> None:
    _write_core(tmp_path, [_item("a", "x", "active")])
    _write_diary(tmp_path, "2026-06-01", "day", "OLD_DAY_MARKER")  # >7d before 07-09
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "OLD_DAY_MARKER" not in out


def test_injects_recent_weekly(tmp_path: Path) -> None:
    _write_core(tmp_path, [_item("a", "x", "active")])
    _write_diary(tmp_path, "2026-06-15", "week", "WEEK_MARKER")  # within ~1mo of 07-09
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "WEEK_MARKER" in out


def test_injects_this_year_monthly_even_if_old(tmp_path: Path) -> None:
    # this-year monthly window: a January entry is still injected in July.
    _write_core(tmp_path, [_item("a", "x", "active")])
    _write_diary(tmp_path, "2026-01-10", "month", "JAN_MONTH_MARKER")
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "JAN_MONTH_MARKER" in out


def test_empty_memory_returns_empty_string(tmp_path: Path) -> None:
    # nothing to inject → empty string → launcher adds no --append-system-prompt.
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert out == ""


def test_no_notes_body_injected(tmp_path: Path) -> None:
    # C-5: notes body never enters the injection (read on demand via dictionary).
    _write_core(tmp_path, [_item("a", "x", "active")])
    MemoryStore(tmp_path).write_note({
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
    })
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "NOTE_SECRET_MARKER" not in out


def test_token_budget_warning_on_oversize(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # C-6: over soft budget logs a warning, does NOT truncate.
    big = "大" * 6000  # ~12K tokens by the CJK estimate → over the 8K budget
    _write_core(tmp_path, [_item("a", big, "active")])
    with caplog.at_level(logging.WARNING):
        out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "大" in out  # not truncated
    assert any(
        "budget" in r.message.lower() or "token" in r.message.lower()
        for r in caplog.records
    )


def test_bad_now_skips_diary_not_core(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # CR(W1): a malformed 'now' (non-zero-padded) logs a warning and skips only
    # the diary section — layer-one is still injected, never blanks the whole thing.
    _write_core(tmp_path, [_item("a", "CORE_MARKER", "active")])
    _write_diary(tmp_path, "2026-07-05", "day", "DAY_MARKER")
    with caplog.at_level(logging.WARNING):
        out = build_memory_injection("2026-7-5", root=tmp_path)  # bad ISO
    assert "CORE_MARKER" in out  # core still injected
    assert "DAY_MARKER" not in out  # diary skipped
    assert any("malformed" in r.message.lower() for r in caplog.records)
