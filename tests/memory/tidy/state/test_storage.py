"""Tidy 水位模型、持久化与保守降级。"""

from __future__ import annotations

import json
from datetime import datetime

from trowel_py.memory.tidy_state import (
    TidyState,
    advance_watermark,
    load_state,
    save_state,
    state_path,
)


class TestStateRoundTrip:
    def test_empty_state(self):
        state = TidyState()
        assert state.weekly_last is None
        assert state.monthly_last is None
        assert state.updated_at is None
        data = state.to_dict()
        assert data["weekly"]["last_successful"] is None
        assert data["monthly"]["last_successful"] is None

    def test_to_from_dict_roundtrip(self):
        state = TidyState(
            weekly_last="2026-W28",
            monthly_last="2026-06",
            updated_at="2026-07-18T03:31:00",
        )
        assert TidyState.from_dict(state.to_dict()) == state

    def test_from_dict_tolerates_missing_scope(self):
        state = TidyState.from_dict({"updated_at": "x"})
        assert state == TidyState(updated_at="x")

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
        state = load_state(tmp_path)
        assert state.weekly_last == "2026-W28"
        assert state.monthly_last == "2026-06"
        assert state.updated_at == "2026-07-18T03:31:00"

    def test_load_corrupt_is_empty_never_all_succeeded(self, tmp_path):
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        assert load_state(tmp_path) == TidyState()

    def test_valid_json_but_malformed_watermark_discards_to_bootstrap(
        self,
        tmp_path,
    ):
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "weekly": {"last_successful": 123},
                    "monthly": {"last_successful": "garbage"},
                }
            ),
            encoding="utf-8",
        )
        state = load_state(tmp_path)
        assert state.weekly_last is None
        assert state.monthly_last is None

    def test_save_writes_atomically_no_tmp_leftover(self, tmp_path):
        state = TidyState(
            weekly_last="2026-W28",
            monthly_last="2026-06",
            updated_at="x",
        )
        save_state(tmp_path, state)
        path = state_path(tmp_path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["weekly"]["last_successful"] == "2026-W28"
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
        advance_watermark(
            tmp_path,
            "weekly",
            "2026-W29",
            now,
        )
        state = load_state(tmp_path)
        assert state.weekly_last == "2026-W29"
        assert state.monthly_last == "2026-06"
        assert state.updated_at == now.isoformat()

    def test_advance_monthly_preserves_weekly(self, tmp_path):
        save_state(
            tmp_path,
            TidyState(
                weekly_last="2026-W28",
                monthly_last="2026-06",
                updated_at="t0",
            ),
        )
        now = datetime(2026, 7, 20, 4, 0)
        advance_watermark(
            tmp_path,
            "monthly",
            "2026-07",
            now,
        )
        state = load_state(tmp_path)
        assert state.monthly_last == "2026-07"
        assert state.weekly_last == "2026-W28"
