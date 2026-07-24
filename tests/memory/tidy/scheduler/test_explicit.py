"""CLI 显式补跑的范围、失败和水位契约。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from trowel_py.memory.tidy_scheduler import run_explicit_catchup
from trowel_py.memory.tidy_state import TidyState, load_state, save_state

from .support import noop_provider, ok_report


class TestRunExplicitCatchup:
    def test_bad_from_period_returns_error_report(self, tmp_path: Path):
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        result = run_explicit_catchup(
            tmp_path,
            "weekly",
            "garbage",
            noop_provider,
            now=datetime(2026, 7, 27, 8, 0),
        )
        assert "error" in result
        assert result["ran"] == []
        assert result["watermark"] == "2026-W29"

    def test_tidy_exception_is_caught_not_raised(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        import trowel_py.memory.tidy as tidy_module

        def boom(root, week, provider):  # noqa: ARG001
            raise RuntimeError("provider 5xx")

        monkeypatch.setattr(tidy_module, "run_weekly_tidy", boom)
        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        result = run_explicit_catchup(
            tmp_path,
            "weekly",
            "2026-W28",
            noop_provider,
            now=datetime(2026, 7, 27, 8, 0),
        )
        assert result["failed_at"] == "2026-W28"
        assert result["ran"] == []
        assert result["watermark"] == "2026-W27"
        assert load_state(tmp_path).weekly_last == "2026-W27"

    def test_advances_watermark_through_range(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        import trowel_py.memory.tidy as tidy_module

        monkeypatch.setattr(
            tidy_module,
            "run_weekly_tidy",
            lambda root, week, provider: ok_report(week),
        )
        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        result = run_explicit_catchup(
            tmp_path,
            "weekly",
            "2026-W28",
            noop_provider,
            now=datetime(2026, 7, 27, 8, 0),
        )
        assert result["ran"] == [
            "2026-W28",
            "2026-W29",
            "2026-W30",
        ]
        assert result["watermark"] == "2026-W30"
        assert load_state(tmp_path).weekly_last == "2026-W30"
