"""验证五项后台任务从真实调度入口选择各自的运行配置。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trowel_py.configuration import background_scheduling
from trowel_py.configuration.background_runtime import BackgroundRuntimeError
from trowel_py.configuration.background_scheduling import BackgroundTaskDispatcher
from trowel_py.configuration.models import TaskId


class RecordingRuntime:
    """记录 dispatcher 每次热读取的 TaskId，并返回可辨认依赖。"""

    def __init__(self) -> None:
        """初始化空调用记录和递增快照版本。"""

        self.tasks: list[TaskId] = []

    def freeze(self, task_id: TaskId) -> SimpleNamespace:
        """返回同时提供 host_factory 与 provider 的最小冻结快照。"""

        self.tasks.append(task_id)
        version = len(self.tasks)

        def host_factory(source_id: object, workdir: Path) -> tuple[object, Path, int]:
            """返回可断言来源、工作目录和热读取版本的 host 标记。"""

            return source_id, workdir, version

        return SimpleNamespace(
            host_factory=host_factory,
            provider=lambda *, workdir: (task_id, workdir, version),
        )


def test_scheduled_review_routes_refine_and_daily(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """定时 Review 分别冻结 refine host 与 Daily provider。"""

    runtime = RecordingRuntime()
    received: list[dict[str, Any]] = []
    monkeypatch.setattr(
        background_scheduling,
        "run_daily_review_sync",
        lambda event: received.append(event),
    )

    BackgroundTaskDispatcher(runtime, tmp_path).dispatch_memory_review({"date": "d"})

    assert runtime.tasks == [TaskId.MEMORY_REFINE, TaskId.MEMORY_DAILY]
    assert received[0]["_host_factory"]("source", tmp_path) == (
        "source",
        tmp_path,
        1,
    )
    assert received[0]["_provider"] == (TaskId.MEMORY_DAILY, tmp_path, 2)


def test_immediate_review_does_not_run_daily_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """带 review_session_id 的即时 Review 只使用 refine 绑定。"""

    runtime = RecordingRuntime()
    received: list[dict[str, Any]] = []
    monkeypatch.setattr(
        background_scheduling,
        "run_daily_review_sync",
        lambda event: received.append(event),
    )

    BackgroundTaskDispatcher(runtime, tmp_path).dispatch_memory_review(
        {"review_session_id": "session-a"}
    )

    assert runtime.tasks == [TaskId.MEMORY_REFINE]
    assert received[0]["_provider"] is None
    assert received[0]["_provider_resolved"] is True


def test_profile_weekly_and_monthly_use_independent_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profile、Weekly 与 Monthly 不能复用另一项任务的 TaskId。"""

    runtime = RecordingRuntime()
    received: list[dict[str, Any]] = []
    monkeypatch.setattr(
        background_scheduling,
        "run_daily_distill_sync",
        lambda event: received.append(event),
    )
    dispatcher = BackgroundTaskDispatcher(runtime, tmp_path)

    dispatcher.dispatch_profile_distill({"date": "d"})
    weekly = dispatcher.weekly_provider()
    monthly = dispatcher.monthly_provider()

    assert runtime.tasks == [
        TaskId.PROFILE_DISTILL,
        TaskId.MEMORY_WEEKLY,
        TaskId.MEMORY_MONTHLY,
    ]
    assert received[0]["_host_factory"]("source", tmp_path)[2] == 1
    assert weekly == (TaskId.MEMORY_WEEKLY, tmp_path, 2)
    assert monthly == (TaskId.MEMORY_MONTHLY, tmp_path, 3)


def test_dispatcher_hot_reads_each_call(tmp_path: Path) -> None:
    """同一调度入口的下一次调用必须取得新冻结快照。"""

    runtime = RecordingRuntime()
    dispatcher = BackgroundTaskDispatcher(runtime, tmp_path)

    first = dispatcher.weekly_provider()
    second = dispatcher.weekly_provider()

    assert first[2] == 1
    assert second[2] == 2
    assert runtime.tasks == [TaskId.MEMORY_WEEKLY, TaskId.MEMORY_WEEKLY]


def test_missing_binding_fails_closed_without_global_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绑定缺失时返回拒绝 host，不能读取历史全局 LLM 配置。"""

    class MissingRuntime:
        """模拟设置域没有可用绑定。"""

        def freeze(self, task_id: TaskId) -> None:
            """对任何 TaskId 明确失败。"""

            raise BackgroundRuntimeError(f"missing {task_id.value}")

    def forbidden_global_config():
        """若生产路由触碰全局回退则立即暴露测试失败。"""

        raise AssertionError("global LLM config must not be loaded")

    monkeypatch.setattr(
        "trowel_py.config.load_llm_config",
        forbidden_global_config,
    )
    dispatcher = BackgroundTaskDispatcher(MissingRuntime(), tmp_path)
    host_factory = dispatcher._host_factory(TaskId.MEMORY_REFINE)

    with pytest.raises(BackgroundRuntimeError, match="missing memory_refine"):
        host_factory("source", tmp_path)
