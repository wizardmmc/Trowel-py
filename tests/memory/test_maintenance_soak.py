from __future__ import annotations

import asyncio
import threading
from datetime import datetime, time, timedelta
from pathlib import Path

from trowel_py.memory.daily_review.scheduler import (
    MemoryReviewScheduler,
    ReviewScheduleConfig,
)
from trowel_py.memory.profile_distill.scheduler import (
    DistillScheduleConfig,
    ProfileDistillScheduler,
)
from trowel_py.memory.tidy_scheduler import TidyScheduler
from trowel_py.memory.tidy_state import TidyState, save_state
from trowel_py.model_os.work_broker import BrokerPolicy, WorkBroker, WorkKind
from tests.memory.tidy.scheduler.support import (
    HangingSleep,
    noop_provider,
    ok_report,
    recording_success,
)


async def test_eight_hour_wake_merges_each_maintenance_period_once(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 7, 27, 9, 0)]
    broker = WorkBroker(
        tmp_path / "model-os.db",
        policy=BrokerPolicy(glm_account_order=("glm",)),
    )
    broker.open()
    try:
        review_calls: list[dict] = []
        distill_calls: list[dict] = []
        tidy_calls: list[str] = []
        review = MemoryReviewScheduler(
            ReviewScheduleConfig(time(2, 30), True),
            tmp_path,
            dispatch_fn=review_calls.append,
            now_fn=lambda: current[0],
            broker=broker,
        )
        distill = ProfileDistillScheduler(
            DistillScheduleConfig(time(2, 30), True),
            tmp_path,
            "http://x",
            dispatch_fn=distill_calls.append,
            now_fn=lambda: current[0],
            broker=broker,
        )
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        tidy = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=lambda: current[0],
            sleep_fn=HangingSleep(),
            weekly_fn=recording_success(tidy_calls),
            monthly_fn=ok_report,
            broker=broker,
        )

        await review._run_once(label="catchup")
        await distill._run_once(label="catchup")
        tidy._catchup_scope_sync("weekly", current[0])
        current[0] += timedelta(hours=8)
        await review._run_once(label="wake")
        await distill._run_once(label="wake")
        tidy._catchup_scope_sync("weekly", current[0])

        assert len(review_calls) == 1
        assert len(distill_calls) == 1
        assert tidy_calls == ["2026-W30"]
        assert broker.usage_totals(work_kind=WorkKind.MAINTENANCE).calls == 3
        assert broker.active_leases() == ()
    finally:
        broker.close()


async def test_review_and_profile_can_hold_separate_slots_concurrently(
    tmp_path: Path,
) -> None:
    broker = WorkBroker(
        tmp_path / "model-os.db",
        policy=BrokerPolicy(
            glm_account_order=("glm",),
            concurrency_per_account=2,
        ),
    )
    broker.open()
    rendezvous = threading.Barrier(2)
    overlap_seen: list[str] = []

    def blocking_dispatch(event: dict) -> None:
        overlap_seen.append(event["root"])
        rendezvous.wait(timeout=1)

    now = datetime(2026, 7, 27, 2, 30)
    review = MemoryReviewScheduler(
        ReviewScheduleConfig(time(2, 30), True),
        tmp_path,
        dispatch_fn=blocking_dispatch,
        now_fn=lambda: now,
        broker=broker,
    )
    distill = ProfileDistillScheduler(
        DistillScheduleConfig(time(2, 30), True),
        tmp_path,
        "http://x",
        dispatch_fn=blocking_dispatch,
        now_fn=lambda: now,
        broker=broker,
    )
    try:
        await asyncio.gather(
            review._run_once(label="daily"),
            distill._run_once(label="daily"),
        )

        assert len(overlap_seen) == 2
        assert len(broker.active_leases()) == 0
        assert broker.usage_totals(work_kind=WorkKind.MAINTENANCE).calls == 2
    finally:
        broker.close()
