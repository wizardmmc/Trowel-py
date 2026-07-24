"""Scheduler 测试的共享时钟与报告。"""

from __future__ import annotations

import asyncio
from datetime import datetime


class HangingSleep:
    async def __call__(self, seconds: float) -> None:  # noqa: ARG002
        await asyncio.Event().wait()


class BudgetSleep:
    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        if len(self.waits) > self.budget:
            raise asyncio.CancelledError


def noop_provider() -> None:
    return None


def ok_report(_period: str) -> dict:
    return {
        "plan_id": "p",
        "compress": {},
        "tidy": {
            "plan_id": "p",
            "applied": [],
            "operations": 0,
        },
    }


def error_report(_period: str) -> dict:
    return {
        "plan_id": "p",
        "tidy": {
            "plan_id": "p",
            "error": "stale",
            "applied": [],
            "operations": 1,
        },
    }


def skipped_report(_period: str) -> dict:
    return {
        "plan_id": "p",
        "skipped": "another tidy is running",
    }


def recording_success(calls: list[str]):
    def run(period: str) -> dict:
        calls.append(period)
        return ok_report(period)

    return run


def now_w31() -> datetime:
    return datetime(2026, 7, 27, 8, 0)
