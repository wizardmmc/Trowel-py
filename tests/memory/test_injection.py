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

from trowel_py.memory.injection import _render_diary, build_memory_injection
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
    # slice-041: daily injected only when in THIS ISO week. 2026-07-08 is in W28.
    _write_diary(tmp_path, "2026-07-08", "day", "RECENT_DAY_MARKER")
    out = build_memory_injection("2026-07-09", root=tmp_path)  # 07-09 is in W28
    assert "RECENT_DAY_MARKER" in out


def test_excludes_daily_beyond_7d(tmp_path: Path) -> None:
    _write_core(tmp_path, [_item("a", "x", "active")])
    _write_diary(tmp_path, "2026-06-01", "day", "OLD_DAY_MARKER")  # >7d before 07-09
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "OLD_DAY_MARKER" not in out


def test_injects_recent_weekly(tmp_path: Path) -> None:
    _write_core(tmp_path, [_item("a", "x", "active")])
    # slice-041: weekly injected when in THIS month but NOT this ISO week.
    # now 2026-07-15 is W29; W28 (Mon 07-06) is in July but not this week.
    _write_diary(tmp_path, "2026-W28", "week", "WEEK_MARKER")
    out = build_memory_injection("2026-07-15", root=tmp_path)
    assert "WEEK_MARKER" in out


def test_injects_this_year_monthly_even_if_old(tmp_path: Path) -> None:
    # this-year monthly window: a January entry is still injected in July.
    _write_core(tmp_path, [_item("a", "x", "active")])
    _write_diary(tmp_path, "2026-01-10", "month", "JAN_MONTH_MARKER")
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "JAN_MONTH_MARKER" in out


def test_empty_memory_returns_memory_root_only(tmp_path: Path) -> None:
    # slice-040-c C-2: even with nothing else, the memory root + tool usage is
    # always injected so the model knows where to query (no longer empty).
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "memory 根路径" in out
    assert "memory.search" in out
    assert "铁律" not in out  # no core content


def test_injection_carries_memory_root_and_tool_usage(tmp_path: Path) -> None:
    """slice-040-c C-2: injection carries the memory root + tool usage."""
    _write_core(tmp_path, [_item("c1", "X", "active")])
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "memory 根路径 + 检索" in out
    assert "memory.search(query)" in out
    assert "memory.read(uri)" in out
    assert str(tmp_path.resolve()) in out
    # slice-052 温和版: tell the model requires_read=true hits MUST be read
    assert "requires_read" in out


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
    # slice-041: budget raised to 30K; need ~40K tokens of CJK to exceed it.
    big = "大" * 20000  # ~40K tokens by the CJK estimate → over the 30K budget
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


# ---------- W4 (codex): injection truncation priority ----------


def test_injection_caps_earlier_monthlies_to_three(tmp_path: Path) -> None:
    """W4: earlier monthlies capped to 3 most recent (no unbounded growth)."""
    for m in ["2025-06", "2025-07", "2025-08", "2025-09", "2025-10"]:
        _write_diary(tmp_path, m, "month", f"month {m}")
    out = build_memory_injection("2026-07-11", root=tmp_path)
    assert "2025-10" in out  # 3 most recent kept
    assert "2025-09" in out
    assert "2025-08" in out
    assert "2025-07" not in out  # beyond cap
    assert "2025-06" not in out


def test_render_diary_include_layers_drops_earlier(tmp_path: Path) -> None:
    """W4: include_layers controls which month tiers are included (truncation)."""
    store = MemoryStore(tmp_path)
    _write_diary(tmp_path, "2026-07-09", "day", "today gotcha")
    _write_diary(tmp_path, "2025-06", "month", "old month flow")
    full = _render_diary(store, "2026-07-11", include_layers=4)
    truncated = _render_diary(store, "2026-07-11", include_layers=1)
    assert "old month flow" in full
    assert "old month flow" not in truncated
    assert "today gotcha" in truncated  # week dailies always kept


# ---------- slice-048: profile injection ----------


def _write_profile(root: Path, **dims: str) -> None:
    """Seed profile.md via the real store write path (round-trips serializer).

    Mirrors the file's other ``_write_*`` helpers: hand-built memory tree under
    tmp_path, no mocks. Uses 047's ``store.write_profile`` so the on-disk shape
    is exactly what production reads.
    """
    from trowel_py.memory.types import Profile

    MemoryStore(root).write_profile(
        Profile(updated="2026-07-14", **dims), source="user-edit"
    )


def test_render_profile_all_five_dims(tmp_path: Path) -> None:
    from trowel_py.memory.injection import _render_profile

    _write_profile(
        tmp_path,
        ability="ABILITY_MARKER",
        methodology="METHOD_MARKER",
        expression="EXPR_MARKER",
        goal="GOAL_MARKER",
        other="OTHER_MARKER",
    )
    out = _render_profile(MemoryStore(tmp_path))
    assert "# 用户画像" in out
    titles = ("能力水平", "方法论偏好", "表达风格", "长程目标", "其他")
    for title in titles:
        assert f"## {title}" in out
    # C-4 canonical order: dims render in _FIELD_TO_TITLE insertion order
    positions = [out.index(f"## {t}") for t in titles]
    assert positions == sorted(positions)
    assert "ABILITY_MARKER" in out
    assert "OTHER_MARKER" in out


def test_render_profile_empty_when_no_file(tmp_path: Path) -> None:
    # C-4: no profile.md → empty_profile() → "" (section omitted, no empty heading)
    from trowel_py.memory.injection import _render_profile

    assert _render_profile(MemoryStore(tmp_path)) == ""


def test_render_profile_empty_when_all_dims_blank(tmp_path: Path) -> None:
    # C-4: a profile that exists but has every dim blank → "" too
    from trowel_py.memory.injection import _render_profile

    _write_profile(tmp_path)  # all five dims default to ""
    assert _render_profile(MemoryStore(tmp_path)) == ""


def test_render_profile_skips_empty_dims(tmp_path: Path) -> None:
    # C-4 (partial): only filled dims render; empty dims' headings are NOT emitted
    from trowel_py.memory.injection import _render_profile

    _write_profile(tmp_path, ability="ABILITY_MARKER", goal="GOAL_MARKER")
    out = _render_profile(MemoryStore(tmp_path))
    assert "# 用户画像" in out
    assert "## 能力水平" in out
    assert "## 长程目标" in out
    assert "ABILITY_MARKER" in out
    assert "GOAL_MARKER" in out
    # canonical order preserved: ability before goal
    assert out.index("能力水平") < out.index("长程目标")
    # skipped (empty) dims: headings must NOT appear
    assert "## 方法论偏好" not in out
    assert "## 表达风格" not in out
    assert "## 其他" not in out


def test_build_injection_includes_profile_between_core_and_l0(tmp_path: Path) -> None:
    _write_core(tmp_path, [_item("a", "CORE_MARKER imperative", "active")])
    _write_profile(tmp_path, ability="PROFILE_MARKER")
    _write_l0(tmp_path, "L0_MARKER index")
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "# 用户画像" in out
    assert "PROFILE_MARKER" in out
    # D1 order: core → profile → L0
    assert out.index("CORE_MARKER") < out.index("PROFILE_MARKER") < out.index("L0_MARKER")


def test_profile_survives_when_diary_truncated(tmp_path: Path) -> None:
    # C-3: profile sits in `sections`; the budget-truncation loop only shrinks
    # diary layers, so profile is never dropped. An oversized earlier-monthly
    # forces the loop to drop it (include_layers 4→3), yet profile remains.
    _write_profile(tmp_path, ability="PROFILE_SURVIVES_MARKER")
    big = "大" * 20000  # ~40K tokens → over the 30K budget at include_layers=4
    _write_diary(tmp_path, "2025-09", "month", big + "BIGMONTHLY_MARKER")  # earlier monthly
    out = build_memory_injection("2026-07-11", root=tmp_path)
    assert "# 用户画像" in out
    assert "PROFILE_SURVIVES_MARKER" in out  # profile kept
    assert "BIGMONTHLY_MARKER" not in out  # earlier monthly dropped by truncation


# ---------- slice-060: memory/profile A/B switches ----------


def _seed_full_memory(root: Path) -> None:
    """Seed one of each layer so every section has a marker to assert on."""
    _write_core(root, [_item("a", "CORE_MARKER imperative", "active")])
    _write_profile(root, ability="PROFILE_MARKER")
    _write_l0(root, "L0_MARKER index")
    _write_diary(root, "2026-07-08", "day", "DAY_MARKER")  # W28, in this week


def test_injection_defaults_to_both_on(tmp_path: Path) -> None:
    # C-1: omitting the switches == current production (memory=on + profile=on).
    _seed_full_memory(tmp_path)
    out = build_memory_injection("2026-07-09", root=tmp_path)
    assert "CORE_MARKER" in out
    assert "# 用户画像" in out
    assert "PROFILE_MARKER" in out
    assert "L0_MARKER" in out
    assert "DAY_MARKER" in out
    assert "memory 根路径" in out


def test_memory_off_drops_all_memory_sections_keeps_profile(tmp_path: Path) -> None:
    # memory=false, profile=true: ONLY the profile section remains. No core /
    # L0 / diary / memory-root, so the model has no read-path pointers either.
    _seed_full_memory(tmp_path)
    out = build_memory_injection(
        "2026-07-09", root=tmp_path, memory_enabled=False, profile_enabled=True
    )
    assert "# 用户画像" in out
    assert "PROFILE_MARKER" in out
    assert "CORE_MARKER" not in out
    assert "L0_MARKER" not in out
    assert "DAY_MARKER" not in out
    assert "memory 根路径" not in out
    assert "memory.search" not in out


def test_profile_off_drops_profile_section_keeps_memory(tmp_path: Path) -> None:
    # memory=true, profile=false: core/L0/diary/root + MCP pointers stay; only
    # the explicit `# 用户画像` heading is gone.
    _seed_full_memory(tmp_path)
    out = build_memory_injection(
        "2026-07-09", root=tmp_path, memory_enabled=True, profile_enabled=False
    )
    assert "CORE_MARKER" in out
    assert "L0_MARKER" in out
    assert "DAY_MARKER" in out
    assert "memory 根路径" in out
    assert "memory.search" in out
    assert "# 用户画像" not in out
    assert "PROFILE_MARKER" not in out


def test_both_off_returns_empty(tmp_path: Path) -> None:
    # memory=false, profile=false: the clean no-personalization baseline. Empty
    # string → launcher adds no --append-system-prompt flag at all.
    _seed_full_memory(tmp_path)
    out = build_memory_injection(
        "2026-07-09", root=tmp_path, memory_enabled=False, profile_enabled=False
    )
    assert out == ""


def test_memory_off_with_no_profile_returns_empty(tmp_path: Path) -> None:
    # memory=false, profile=true but there is no profile.md → nothing to inject.
    _seed_full_memory(tmp_path)
    (tmp_path / "profile.md").unlink()
    out = build_memory_injection(
        "2026-07-09", root=tmp_path, memory_enabled=False, profile_enabled=True
    )
    assert out == ""
