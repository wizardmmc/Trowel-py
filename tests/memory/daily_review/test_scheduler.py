from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, time, timedelta, timezone

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.memory.daily_review.scheduler import (
    DEFAULT_REVIEW_ENABLED,
    DEFAULT_REVIEW_TIME,
    MemoryReviewScheduler,
    ReviewScheduleConfig,
    load_review_config,
)
from trowel_py.memory.scheduling import seconds_until


@pytest.fixture(autouse=True)
def _reset_register_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离进程级注册状态，避免测试顺序影响首次注册路径。"""
    import trowel_py.memory.daily_review.scheduler as rs

    monkeypatch.setattr(rs, "_REVIEW_JOB_REGISTERED", False)


class TestSecondsUntil:
    def test_same_day_when_target_ahead(self):
        assert seconds_until(time(2, 30), datetime(2026, 7, 13, 1, 0)) == pytest.approx(
            5400
        )

    def test_cross_midnight_when_target_passed(self):
        assert seconds_until(time(2, 30), datetime(2026, 7, 13, 3, 0)) == pytest.approx(
            84600
        )

    def test_exact_now_rolls_to_tomorrow(self):
        assert seconds_until(
            time(2, 30), datetime(2026, 7, 13, 2, 30, 0)
        ) == pytest.approx(86400)


class TestLoadReviewConfig:
    def test_defaults_when_no_memory_section(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[llm]\nactive = "x"\n[llm.x]\nbase_url = "u"\n')
        c = load_review_config(cfg)
        assert c.review_time == DEFAULT_REVIEW_TIME
        assert c.review_enabled is DEFAULT_REVIEW_ENABLED

    def test_override_time_and_enabled(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[memory]\nreview_time = "03:15"\nreview_enabled = false\n')
        c = load_review_config(cfg)
        assert c.review_time == time(3, 15)
        assert c.review_enabled is False

    def test_invalid_time_falls_back(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[memory]\nreview_time = "not-a-time"\n')
        c = load_review_config(cfg)
        assert c.review_time == DEFAULT_REVIEW_TIME

    def test_missing_config_file_uses_defaults(self, tmp_path):
        c = load_review_config(tmp_path / "does-not-exist.toml")
        assert c.review_time == DEFAULT_REVIEW_TIME
        assert c.review_enabled is True


def _cfg(*, enabled: bool = True, t: time | None = None) -> ReviewScheduleConfig:
    return ReviewScheduleConfig(review_time=t or time(2, 30), review_enabled=enabled)


class _HangingSleep:
    async def __call__(self, seconds: float) -> None:  # noqa: ARG002
        await asyncio.Event().wait()


class _BudgetSleep:
    """耗尽预算后复用调度器原生取消路径，避免为测试增加专用异常。"""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        if len(self.waits) > self.budget:
            raise asyncio.CancelledError


async def _wait_until(predicate, *, timeout: float = 1.0) -> bool:
    """轮询线程派发结果，避免固定 sleep 在慢速 CI 上产生竞态。"""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.01)
        elapsed += 0.01
    return False


class TestSchedulerRunOnce:
    async def test_run_once_dispatches_with_root(self, tmp_path):
        calls: list[dict] = []
        sched = MemoryReviewScheduler(_cfg(), tmp_path, dispatch_fn=calls.append)
        await sched._run_once()
        assert len(calls) == 1
        assert calls[0]["root"] == str(tmp_path)
        assert "date" in calls[0]

    async def test_run_once_dispatches_closed_date_window(self, tmp_path):
        calls: list[dict] = []
        sched = MemoryReviewScheduler(
            _cfg(),
            tmp_path,
            dispatch_fn=calls.append,
            now_fn=lambda: datetime(2026, 7, 24, 9, 15),
        )

        await sched._run_once(label="catchup")

        assert calls == [
            {
                "date": "2026-07-23",
                "eligible_before": "2026-07-24T00:00:00",
                "root": str(tmp_path),
            }
        ]

    async def test_run_once_normalizes_aware_now_to_local_wall_clock(self, tmp_path):
        calls: list[dict] = []
        cst = timezone(timedelta(hours=8))
        sched = MemoryReviewScheduler(
            _cfg(),
            tmp_path,
            dispatch_fn=calls.append,
            now_fn=lambda: datetime(2026, 7, 24, 9, 15, tzinfo=cst),
        )

        await sched._run_once()

        assert calls[0]["eligible_before"] == "2026-07-24T00:00:00"

    async def test_run_once_swallows_dispatch_exception(self, tmp_path):
        def boom(_event: dict) -> None:
            raise RuntimeError("cc exploded")

        sched = MemoryReviewScheduler(_cfg(), tmp_path, dispatch_fn=boom)
        await sched._run_once()


class TestSchedulerStartStop:
    async def test_disabled_keeps_only_immediate_worker(self, tmp_path):
        sched = MemoryReviewScheduler(
            _cfg(enabled=False),
            tmp_path,
            dispatch_fn=lambda _e: None,
            sleep_fn=_HangingSleep(),
        )
        await sched.start()
        assert len(sched.tasks) == 1
        await sched.stop()

    async def test_start_creates_immediate_and_two_daily_tasks(self, tmp_path):
        sched = MemoryReviewScheduler(
            _cfg(), tmp_path, dispatch_fn=lambda _e: None, sleep_fn=_HangingSleep()
        )
        await sched.start()
        assert len(sched.tasks) == 3
        await sched.stop()
        assert sched.tasks == ()

    async def test_start_is_idempotent(self, tmp_path):
        sched = MemoryReviewScheduler(
            _cfg(), tmp_path, dispatch_fn=lambda _e: None, sleep_fn=_HangingSleep()
        )
        await sched.start()
        n = len(sched.tasks)
        await sched.start()
        assert len(sched.tasks) == n
        await sched.stop()

    async def test_catchup_fires_once_on_start(self, tmp_path):
        calls: list[dict] = []
        sched = MemoryReviewScheduler(
            _cfg(), tmp_path, dispatch_fn=calls.append, sleep_fn=_HangingSleep()
        )
        await sched.start()
        fired = await _wait_until(lambda: len(calls) >= 1)
        await sched.stop()
        assert fired
        assert len(calls) == 1
        assert calls[0]["root"] == str(tmp_path)

    async def test_close_request_is_persisted_before_background_dispatch(
        self, tmp_path
    ):
        calls: list[dict] = []
        now = [datetime(2026, 8, 1, 10, 0)]
        sched = MemoryReviewScheduler(
            _cfg(enabled=False),
            tmp_path,
            dispatch_fn=calls.append,
            now_fn=lambda: now[0],
            sleep_fn=_HangingSleep(),
        )
        await sched.start()
        binding = make_binding(
            session_id="agent-1",
            runtime=Runtime.CODEX,
            native_session_id="thread-1",
            workdir=str(tmp_path),
            model="gpt-5.6-sol",
            effort="high",
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=(),
            name="test",
            session_kind="user",
        )

        sched.request_session_review(binding)

        from trowel_py.memory.sessions_repo import (
            create_sessions_repository,
            open_sessions_db,
        )

        conn = open_sessions_db(tmp_path)
        try:
            assert (
                create_sessions_repository(conn).review_requests.find("agent-1")
                is not None
            )
        finally:
            conn.close()
        await asyncio.sleep(0.02)
        assert not any(call.get("review_session_id") == "agent-1" for call in calls)
        now[0] = datetime(2026, 8, 1, 10, 5)
        sched._immediate_wakeup.set()
        fired = await _wait_until(
            lambda: any(call.get("review_session_id") == "agent-1" for call in calls)
        )
        await sched.stop()
        assert fired

    async def test_immediate_query_uses_naive_local_wall_clock(
        self, tmp_path, monkeypatch
    ):
        """即时队列查询应与持久化截止时间使用相同的无时区本地文本。"""

        from trowel_py.memory.daily_review import scheduler as rs_mod

        eligible_values: list[str | None] = []

        def load_requests(_root, *, eligible_at=None):
            eligible_values.append(eligible_at)
            return []

        monkeypatch.setattr(rs_mod, "load_session_review_requests", load_requests)
        cst = timezone(timedelta(hours=8))
        sched = MemoryReviewScheduler(
            _cfg(enabled=False),
            tmp_path,
            now_fn=lambda: datetime(2026, 8, 1, 10, 5, tzinfo=cst),
        )
        sched._immediate_wakeup.set()

        task = asyncio.create_task(sched._immediate_loop())
        assert await _wait_until(lambda: bool(eligible_values))
        task.cancel()
        await task

        assert eligible_values[0] == "2026-08-01T10:05:00.000000"

    async def test_start_resumes_persisted_close_request(self, tmp_path):
        from trowel_py.memory.sessions_repo import (
            create_sessions_repository,
            open_sessions_db,
        )

        conn = open_sessions_db(tmp_path)
        repo = create_sessions_repository(conn)
        repo.review_requests.enqueue(
            "agent-before-restart",
            runtime="claude_code",
            requested_at="2026-07-31T10:00:00",
        )
        conn.close()
        calls: list[dict] = []
        sched = MemoryReviewScheduler(
            _cfg(enabled=False),
            tmp_path,
            dispatch_fn=calls.append,
            sleep_fn=_HangingSleep(),
        )

        await sched.start()
        fired = await _wait_until(
            lambda: any(
                call.get("review_session_id") == "agent-before-restart"
                for call in calls
            )
        )
        await sched.stop()

        assert fired

    async def test_immediate_worker_retries_persisted_request(self, tmp_path, monkeypatch):
        from trowel_py.memory.daily_review import scheduler as rs_mod
        from trowel_py.memory.sessions_repo import (
            create_sessions_repository,
            open_sessions_db,
        )

        monkeypatch.setattr(rs_mod, "IMMEDIATE_RETRY_MIN_SECONDS", 0.01)
        monkeypatch.setattr(rs_mod, "IMMEDIATE_RETRY_MAX_SECONDS", 0.02)
        attempts = 0

        def dispatch(event: dict) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary failure")
            conn = open_sessions_db(tmp_path)
            try:
                create_sessions_repository(conn).review_requests.complete(
                    event["review_session_id"]
                )
            finally:
                conn.close()

        conn = open_sessions_db(tmp_path)
        create_sessions_repository(conn).review_requests.enqueue(
            "agent-retry",
            runtime="codex",
            requested_at="2026-07-31T10:00:00",
        )
        conn.close()
        sched = MemoryReviewScheduler(
            _cfg(enabled=False),
            tmp_path,
            dispatch_fn=dispatch,
        )

        await sched.start()
        retried = await _wait_until(lambda: attempts >= 2)
        await sched.stop()

        assert retried

    async def test_lock_busy_request_retries_after_real_review_lock_releases(
        self, tmp_path, monkeypatch
    ):
        fcntl = pytest.importorskip("fcntl")
        from trowel_py.memory import review_job
        from trowel_py.memory.daily_review import scheduler as rs_mod
        from trowel_py.memory.sessions_repo import (
            create_sessions_repository,
            open_sessions_db,
        )

        monkeypatch.setattr(rs_mod, "IMMEDIATE_RETRY_MIN_SECONDS", 0.01)
        monkeypatch.setattr(rs_mod, "IMMEDIATE_RETRY_MAX_SECONDS", 0.02)
        attempts = 0
        entered = 0

        async def fake_review_body(
            root,
            _date_str,
            _host_factory,
            _provider,
            _eligible_before,
            review_session_id,
        ) -> None:
            nonlocal entered
            entered += 1
            conn = open_sessions_db(root)
            try:
                create_sessions_repository(conn).review_requests.complete(
                    review_session_id
                )
            finally:
                conn.close()

        monkeypatch.setattr(review_job, "_run_daily_review_locked", fake_review_body)

        def dispatch(event: dict) -> None:
            nonlocal attempts
            attempts += 1
            review_job.run_daily_review_sync(event)

        conn = open_sessions_db(tmp_path)
        create_sessions_repository(conn).review_requests.enqueue(
            "agent-lock-retry",
            runtime="codex",
            requested_at="2026-07-31T10:00:00",
        )
        conn.close()
        lock_path = tmp_path / "meta" / ".review.lock"
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sched = MemoryReviewScheduler(
            _cfg(enabled=False),
            tmp_path,
            dispatch_fn=dispatch,
        )
        try:
            await sched.start()
            assert await _wait_until(lambda: attempts >= 2)
            assert entered == 0
            conn = open_sessions_db(tmp_path)
            try:
                assert (
                    create_sessions_repository(conn).review_requests.find(
                        "agent-lock-retry"
                    )
                    is not None
                )
            finally:
                conn.close()

            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            assert await _wait_until(lambda: entered == 1)
            conn = open_sessions_db(tmp_path)
            try:
                assert (
                    create_sessions_repository(conn).review_requests.find(
                        "agent-lock-retry"
                    )
                    is None
                )
            finally:
                conn.close()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            await sched.stop()

    async def test_stop_does_not_wait_for_active_thread_dispatch(self, tmp_path):
        from trowel_py.memory.sessions_repo import (
            create_sessions_repository,
            open_sessions_db,
        )

        started = threading.Event()
        release = threading.Event()

        def dispatch(_event: dict) -> None:
            started.set()
            release.wait(timeout=2)

        conn = open_sessions_db(tmp_path)
        try:
            create_sessions_repository(conn).review_requests.enqueue(
                "agent-drain",
                runtime="codex",
                requested_at="2026-07-31T10:00:00",
            )
        finally:
            conn.close()
        sched = MemoryReviewScheduler(
            _cfg(enabled=False),
            tmp_path,
            dispatch_fn=dispatch,
        )
        await sched.start()
        assert await _wait_until(started.is_set)

        stopping = asyncio.create_task(sched.stop())
        await asyncio.wait_for(stopping, timeout=0.1)
        release.set()


class TestSchedulerDailyLoop:
    async def test_loop_dispatches_each_interval(self, tmp_path):
        calls: list[dict] = []
        sleep = _BudgetSleep(budget=2)
        sched = MemoryReviewScheduler(
            _cfg(t=time(2, 30)),
            tmp_path,
            dispatch_fn=calls.append,
            now_fn=lambda: datetime(2026, 7, 13, 1, 0),
            sleep_fn=sleep,
        )
        await sched._daily_loop()
        assert len(calls) == 2
        assert all(w == pytest.approx(5400) for w in sleep.waits[:2])

    async def test_loop_uses_configured_time(self, tmp_path):
        calls: list[dict] = []
        sleep = _BudgetSleep(budget=1)
        sched = MemoryReviewScheduler(
            _cfg(t=time(3, 15)),
            tmp_path,
            dispatch_fn=calls.append,
            now_fn=lambda: datetime(2026, 7, 13, 3, 0),
            sleep_fn=sleep,
        )
        await sched._daily_loop()
        assert len(calls) == 1
        assert sleep.waits[0] == pytest.approx(900)


class TestLifespanIntegration:
    def test_startup_starts_scheduler(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory.daily_review import scheduler as rs_mod

        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: tmp_path)
        monkeypatch.setattr(rs_mod, "_default_dispatch", lambda _e: None)
        app = create_app()
        with TestClient(app):
            sched = app.state.memory_scheduler
            assert sched is not None
            assert sched._started is True
            assert len(sched.tasks) == 3
        assert app.state.memory_scheduler.tasks == ()

    def test_disabled_starts_only_immediate_worker(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory.daily_review import scheduler as rs_mod

        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: tmp_path)
        monkeypatch.setattr(
            rs_mod,
            "load_review_config",
            lambda *_a, **_k: rs_mod.ReviewScheduleConfig(
                rs_mod.DEFAULT_REVIEW_TIME, False
            ),
        )
        app = create_app()
        with TestClient(app):
            sched = app.state.memory_scheduler
            assert sched is not None
            assert sched._started is True
            assert len(sched.tasks) == 1

    @pytest.mark.parametrize("runtime", ["claude_code", "codex"])
    def test_delete_route_persists_without_dispatch_before_deadline(
        self, tmp_path, monkeypatch, runtime
    ):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory.daily_review import scheduler as rs_mod
        from trowel_py.memory.sessions_repo import (
            create_sessions_repository,
            open_sessions_db,
        )

        memory_root = tmp_path / "memory"
        workdir = tmp_path / "project"
        workdir.mkdir()
        dispatched: list[dict] = []
        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: memory_root)
        monkeypatch.setattr(rs_mod, "_default_dispatch", dispatched.append)
        monkeypatch.setattr(
            rs_mod,
            "load_review_config",
            lambda *_args, **_kwargs: rs_mod.ReviewScheduleConfig(
                rs_mod.DEFAULT_REVIEW_TIME,
                False,
            ),
        )
        app = create_app()
        with TestClient(app) as client:
            created = client.post(
                "/api/agent/sessions",
                json={"runtime": runtime, "workdir": str(workdir)},
            )
            assert created.status_code == 200
            session_id = created.json()["data"]["session_id"]

            closed = client.delete(f"/api/agent/sessions/{session_id}")

            assert closed.status_code == 200
            asyncio.run(asyncio.sleep(0.05))
            assert not any(
                event.get("review_session_id") == session_id
                for event in dispatched
            )

        conn = open_sessions_db(memory_root)
        try:
            request = create_sessions_repository(conn).review_requests.find(session_id)
            assert request is not None
            assert request.runtime == runtime
            assert datetime.fromisoformat(request.not_before) - datetime.fromisoformat(
                request.requested_at
            ) == timedelta(minutes=5)
        finally:
            conn.close()

    def test_close_still_persists_when_scheduler_start_fails(
        self, tmp_path, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory.daily_review import scheduler as rs_mod
        from trowel_py.memory.sessions_repo import (
            create_sessions_repository,
            open_sessions_db,
        )

        memory_root = tmp_path / "memory"
        workdir = tmp_path / "project"
        workdir.mkdir()
        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: memory_root)

        async def fail_start(_self) -> None:
            raise RuntimeError("scheduler unavailable")

        monkeypatch.setattr(rs_mod.MemoryReviewScheduler, "start", fail_start)
        app = create_app()
        with TestClient(app) as client:
            created = client.post(
                "/api/agent/sessions",
                json={"runtime": "codex", "workdir": str(workdir)},
            )
            assert created.status_code == 200
            session_id = created.json()["data"]["session_id"]

            closed = client.delete(f"/api/agent/sessions/{session_id}")

            assert closed.status_code == 200
            assert app.state.memory_scheduler is None

        conn = open_sessions_db(memory_root)
        try:
            request = create_sessions_repository(conn).review_requests.find(session_id)
            assert request is not None
            assert request.runtime == "codex"
        finally:
            conn.close()
