"""tests for the tidy success-watermark + period enumeration (slice-063).

The watermark is the per-scope ``last_successful`` period (ISO week / month).
State lives at ``<root>/meta/tidy-state.json`` and is replaced atomically; a
corrupt or missing file is NEVER read as "everything succeeded" — it bootstraps
to empty so the next run catches up only the most recent completed period
(C-6/C-8). Period enumeration is pure: given the watermark and ``now`` it
returns the completed periods to catch up, oldest-first, never the in-progress
week/month (C-1/C-2/C-3/C-4).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from trowel_py.memory.tidy_state import (
    MAX_PENDING_MONTHS,
    MAX_PENDING_WEEKS,
    TidyState,
    advance_watermark,
    enumerate_pending_months,
    enumerate_pending_weeks,
    last_iso_week,
    last_month,
    load_state,
    next_iso_week,
    next_month,
    save_state,
    state_path,
)


# ---------- T1: state round-trip + persistence ----------


class TestStateRoundTrip:
    def test_empty_state(self):
        s = TidyState()
        assert s.weekly_last is None
        assert s.monthly_last is None
        assert s.updated_at is None
        d = s.to_dict()
        assert d["weekly"]["last_successful"] is None
        assert d["monthly"]["last_successful"] is None

    def test_to_from_dict_roundtrip(self):
        s = TidyState(
            weekly_last="2026-W28",
            monthly_last="2026-06",
            updated_at="2026-07-18T03:31:00",
        )
        assert TidyState.from_dict(s.to_dict()) == s

    def test_from_dict_tolerates_missing_scope(self):
        # a half-written / hand-edited file must not crash; missing → None
        s = TidyState.from_dict({"updated_at": "x"})
        assert s == TidyState(updated_at="x")

    def test_load_missing_is_empty(self, tmp_path):
        assert load_state(tmp_path) == TidyState()

    def test_load_reads_existing(self, tmp_path):
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "weekly": {"last_successful": "2026-W28"},
                    "monthly": {"last_successful": "2026-06"},
                    "updated_at": "2026-07-18T03:31:00",
                }
            ),
            encoding="utf-8",
        )
        s = load_state(tmp_path)
        assert s.weekly_last == "2026-W28"
        assert s.monthly_last == "2026-06"
        assert s.updated_at == "2026-07-18T03:31:00"

    def test_load_corrupt_is_empty_never_all_succeeded(self, tmp_path):
        # C-6: corrupt JSON MUST NOT be read as "all succeeded"
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        assert load_state(tmp_path) == TidyState()

    def test_valid_json_but_malformed_watermark_discards_to_bootstrap(
        self, tmp_path
    ):
        # C-6: valid JSON with a malformed watermark value must not crash
        # --status / catchup — discard the bad value → conservative bootstrap.
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "weekly": {"last_successful": 123},  # not a string
                    "monthly": {"last_successful": "garbage"},  # bad format
                }
            ),
            encoding="utf-8",
        )
        s = load_state(tmp_path)
        assert s.weekly_last is None
        assert s.monthly_last is None

    def test_save_writes_atomically_no_tmp_leftover(self, tmp_path):
        s = TidyState(
            weekly_last="2026-W28", monthly_last="2026-06", updated_at="x"
        )
        save_state(tmp_path, s)
        path = state_path(tmp_path)
        assert path.exists()
        assert (
            json.loads(path.read_text(encoding="utf-8"))["weekly"][
                "last_successful"
            ]
            == "2026-W28"
        )
        # no half-written tmp file survives the atomic replace
        assert not path.with_suffix(".json.tmp").exists()

    def test_advance_preserves_other_scope(self, tmp_path):
        save_state(
            tmp_path,
            TidyState(
                weekly_last="2026-W28",
                monthly_last="2026-06",
                updated_at="t0",
            ),
        )
        now = datetime(2026, 7, 20, 3, 30)
        advance_watermark(tmp_path, "weekly", "2026-W29", now)
        s = load_state(tmp_path)
        assert s.weekly_last == "2026-W29"
        assert s.monthly_last == "2026-06"  # untouched
        assert s.updated_at == now.isoformat()

    def test_advance_monthly_preserves_weekly(self, tmp_path):
        save_state(
            tmp_path,
            TidyState(
                weekly_last="2026-W28", monthly_last="2026-06", updated_at="t0"
            ),
        )
        now = datetime(2026, 7, 20, 4, 0)
        advance_watermark(tmp_path, "monthly", "2026-07", now)
        s = load_state(tmp_path)
        assert s.monthly_last == "2026-07"
        assert s.weekly_last == "2026-W28"


# ---------- T2: period math (next / parse) ----------


class TestNextPeriod:
    def test_next_iso_week_simple(self):
        assert next_iso_week("2026-W28") == "2026-W29"

    def test_next_iso_week_cross_year(self):
        # 2026 has a W53 (Jan 1 is Thursday) — the week after W53 is 2027-W01
        assert next_iso_week("2026-W53") == "2027-W01"

    def test_next_iso_week_into_w53_year(self):
        # 2020 has a W53 — the week after W52 is W53, not next year
        assert next_iso_week("2020-W52") == "2020-W53"

    def test_next_month_simple(self):
        assert next_month("2026-06") == "2026-07"

    def test_next_month_jan_to_feb(self):
        assert next_month("2026-01") == "2026-02"

    def test_next_month_cross_year(self):
        assert next_month("2026-12") == "2027-01"

    def test_parse_rejects_garbage(self):
        with pytest.raises(ValueError):
            next_iso_week("nope")
        with pytest.raises(ValueError):
            next_month("2026-13")
        with pytest.raises(ValueError):
            next_month("2026/06")


# ---------- T3: pending enumeration — weekly ----------


class TestEnumeratePendingWeeks:
    def test_bootstrap_only_most_recent(self):
        # C-8: no watermark → only the most recent completed week
        now = datetime(2026, 7, 27, 8, 0)  # Mon W31
        assert last_iso_week(now) == "2026-W30"
        assert enumerate_pending_weeks(None, now) == ["2026-W30"]

    def test_three_week_gap_spec_scenario(self):
        # watermark stuck at W27 (3 missed mondays); 4th week open → W28..W30
        now = datetime(2026, 7, 27, 8, 0)  # end = W30
        assert enumerate_pending_weeks("2026-W27", now) == [
            "2026-W28",
            "2026-W29",
            "2026-W30",
        ]

    def test_last_equals_end_no_rerun(self):
        # already at the most-recent completed week → nothing pending
        now = datetime(2026, 7, 27, 8, 0)
        assert enumerate_pending_weeks("2026-W30", now) == []

    def test_cross_year(self):
        # last=2026-W53 (2026 has W53), now in 2027-W03 → end=2027-W02 → W01,W02
        now = datetime(2027, 1, 20, 3, 30)  # Tue, W03; now-7d → W02
        assert last_iso_week(now) == "2027-W02"
        assert enumerate_pending_weeks("2026-W53", now) == [
            "2027-W01",
            "2027-W02",
        ]

    def test_never_includes_in_progress_week(self):
        # C-2: the in-progress week (the one containing `now`) is never pending.
        # now=W31, so even a stale watermark at W29 can't reach W31.
        now = datetime(2026, 7, 27, 8, 0)  # W31
        pending = enumerate_pending_weeks("2026-W29", now)
        assert pending == ["2026-W30"]
        assert "2026-W31" not in pending

    def test_watermark_ahead_of_now_is_empty(self):
        # corrupt/future watermark → no pending (don't rewind)
        now = datetime(2026, 7, 27, 8, 0)  # end=W30
        assert enumerate_pending_weeks("2026-W40", now) == []

    def test_capped_so_corrupt_watermark_cannot_loop_forever(self):
        # a pathological ancient watermark must hit the safety cap, not hang
        now = datetime(2026, 7, 27, 8, 0)
        pending = enumerate_pending_weeks("2000-W01", now)
        assert len(pending) == MAX_PENDING_WEEKS

    def test_bad_watermark_format_raises(self):
        # a garbage watermark is a programmer/config error, not silently empty
        now = datetime(2026, 7, 27, 8, 0)
        with pytest.raises(ValueError):
            enumerate_pending_weeks("garbage", now)


# ---------- T3b: pending enumeration — monthly ----------


class TestEnumeratePendingMonths:
    def test_bootstrap_only_most_recent(self):
        now = datetime(2026, 8, 15, 4, 0)
        assert last_month(now) == "2026-07"
        assert enumerate_pending_months(None, now) == ["2026-07"]

    def test_one_month_gap(self):
        now = datetime(2026, 8, 15, 4, 0)  # end=2026-07
        assert enumerate_pending_months("2026-06", now) == ["2026-07"]

    def test_cross_year(self):
        now = datetime(2027, 2, 15, 4, 0)  # end=2027-01
        assert enumerate_pending_months("2026-11", now) == [
            "2026-12",
            "2027-01",
        ]

    def test_last_equals_end_no_rerun(self):
        now = datetime(2026, 8, 15, 4, 0)
        assert enumerate_pending_months("2026-07", now) == []

    def test_never_includes_in_progress_month(self):
        # now=August → August is in-progress, never pending
        now = datetime(2026, 8, 15, 4, 0)
        assert "2026-08" not in enumerate_pending_months("2026-05", now)

    def test_capped(self):
        # ~2700 months > 1200 cap → must hit the cap, not enumerate all
        now = datetime(2026, 8, 15, 4, 0)
        pending = enumerate_pending_months("1800-01", now)
        assert len(pending) == MAX_PENDING_MONTHS
