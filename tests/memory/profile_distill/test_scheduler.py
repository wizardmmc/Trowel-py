from __future__ import annotations

import asyncio
from datetime import datetime, time
from pathlib import Path

import pytest

from trowel_py.memory.profile_distill.scheduler import (
    DEFAULT_DISTILL_ENABLED,
    DEFAULT_DISTILL_TIME,
    DistillScheduleConfig,
    ProfileDistillScheduler,
    load_distill_config,
)


class _BlockingSleep:
    """记录等待时间后挂起，让测试只观察一次调度并由 stop 取消。"""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        await asyncio.Future()


async def _poll(predicate, *, tries: int = 50, delay: float = 0.01) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return False


class TestLoadDistillConfig:
    def test_defaults_when_no_memory_section(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[llm]\nactive = "x"\n')
        c = load_distill_config(cfg)
        assert c.distill_time == DEFAULT_DISTILL_TIME
        assert c.distill_enabled is DEFAULT_DISTILL_ENABLED

    def test_override_time_and_enabled(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[memory]\ndistill_time = "04:05"\ndistill_enabled = false\n')
        c = load_distill_config(cfg)
        assert c.distill_time == time(4, 5)
        assert c.distill_enabled is False

    def test_invalid_time_falls_back(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[memory]\ndistill_time = "nope"\n')
        assert load_distill_config(cfg).distill_time == DEFAULT_DISTILL_TIME

    def test_nonbool_enabled_falls_back(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[memory]\ndistill_enabled = "false"\n')
        assert load_distill_config(cfg).distill_enabled is DEFAULT_DISTILL_ENABLED


class TestSchedulerDispatch:
    async def test_catchup_dispatches_with_proxy(self, tmp_path: Path) -> None:
        events: list[dict] = []
        cfg = DistillScheduleConfig(DEFAULT_DISTILL_TIME, True)
        sched = ProfileDistillScheduler(
            cfg,
            tmp_path,
            "http://127.0.0.1:8000",
            dispatch_fn=events.append,
            now_fn=lambda: datetime(2026, 7, 15, 1, 0),
            sleep_fn=_BlockingSleep(),
        )
        await sched.start()
        try:
            assert await _poll(lambda: any("proxy_base_url" in e for e in events))
            catchup = next(e for e in events if "proxy_base_url" in e)
            assert catchup["proxy_base_url"] == "http://127.0.0.1:8000"
            assert catchup["root"] == str(tmp_path)
            assert "date" in catchup
        finally:
            await sched.stop()

    async def test_disabled_does_not_schedule(self, tmp_path: Path) -> None:
        events: list[dict] = []
        cfg = DistillScheduleConfig(DEFAULT_DISTILL_TIME, False)
        sched = ProfileDistillScheduler(
            cfg,
            tmp_path,
            "http://x",
            dispatch_fn=events.append,
            now_fn=lambda: datetime(2026, 7, 15, 1, 0),
            sleep_fn=_BlockingSleep(),
        )
        await sched.start()
        assert sched.tasks == ()
        assert events == []
        await sched.stop()

    async def test_daily_loop_waits_until_target(self, tmp_path: Path) -> None:
        events: list[dict] = []
        cfg = DistillScheduleConfig(time(2, 50), True)
        sleep = _BlockingSleep()
        sched = ProfileDistillScheduler(
            cfg,
            tmp_path,
            "http://x",
            dispatch_fn=events.append,
            now_fn=lambda: datetime(2026, 7, 15, 1, 0),
            sleep_fn=sleep,
        )
        await sched.start()
        try:
            assert await _poll(lambda: bool(sleep.waits))
            assert sleep.waits[0] == pytest.approx(6600, rel=0.01)
        finally:
            await sched.stop()

    async def test_failure_does_not_crash(self, tmp_path: Path) -> None:
        cfg = DistillScheduleConfig(DEFAULT_DISTILL_TIME, True)

        def boom(_event: dict) -> None:
            raise RuntimeError("distill blew up")

        sched = ProfileDistillScheduler(
            cfg,
            tmp_path,
            "http://x",
            dispatch_fn=boom,
            now_fn=lambda: datetime(2026, 7, 15, 1, 0),
            sleep_fn=_BlockingSleep(),
        )
        await sched.start()
        await asyncio.sleep(0.02)
        assert sched._started is True
        await sched.stop()
