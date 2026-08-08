"""组装五项后台任务调度器，并在每次运行边界注入冻结运行配置。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trowel_py.configuration.background_runtime import (
    BackgroundRuntimeError,
    BackgroundRuntimeManager,
)
from trowel_py.configuration.models import TaskId
from trowel_py.llm.client import LLMProvider
from trowel_py.memory.daily_review.scheduler import MemoryReviewScheduler
from trowel_py.memory.review_job import run_daily_review_sync
from trowel_py.memory.tidy_scheduler import TidyScheduler
from trowel_py.profile.distill.batch import run_daily_distill_sync
from trowel_py.profile.distill.scheduler import ProfileDistillScheduler
from trowel_py.resource_lifecycle.registry import ResourceRegistry

logger = logging.getLogger(__name__)
HostFactory = Callable[[object, Path], Any]


class BackgroundTaskDispatcher:
    """把调度事件与对应任务的本次运行配置组合起来。"""

    def __init__(
        self,
        runtime: BackgroundRuntimeManager,
        memory_root: Path,
    ) -> None:
        """保存无状态 runtime 解析器和后台任务受控工作目录。

        Args:
            runtime: 每次 freeze 都热读取设置域绑定的 runtime 管理器。
            memory_root: Daily 与 Tidy 使用的 Memory 根目录。
        """

        self._runtime = runtime
        self._memory_root = memory_root

    def dispatch_memory_review(self, event: dict[str, Any]) -> None:
        """为一次 Review 冻结 refine host 和可选 Daily provider。"""

        scoped = dict(event)
        scoped["_host_factory"] = self._host_factory(TaskId.MEMORY_REFINE)
        scoped["_provider_resolved"] = True
        scoped["_provider"] = None
        if not scoped.get("review_session_id"):
            try:
                scoped["_provider"] = self._runtime.freeze(
                    TaskId.MEMORY_DAILY
                ).provider(workdir=self._memory_root)
            except Exception as exc:  # noqa: BLE001 - Daily 允许降级为纯聚合。
                self._log_unavailable(TaskId.MEMORY_DAILY, exc)
        run_daily_review_sync(scoped)

    def dispatch_profile_distill(self, event: dict[str, Any]) -> None:
        """为一次 Profile 提炼冻结对应 Agent host。"""

        scoped = dict(event)
        scoped["_host_factory"] = self._host_factory(TaskId.PROFILE_DISTILL)
        run_daily_distill_sync(scoped)

    def weekly_provider(self) -> LLMProvider:
        """热读取并冻结一次每周 Memory 整理配置。"""

        return self._runtime.freeze(TaskId.MEMORY_WEEKLY).provider(
            workdir=self._memory_root
        )

    def monthly_provider(self) -> LLMProvider:
        """热读取并冻结一次每月 Memory 整理配置。"""

        return self._runtime.freeze(TaskId.MEMORY_MONTHLY).provider(
            workdir=self._memory_root
        )

    def _host_factory(self, task_id: TaskId) -> HostFactory:
        """冻结 Agent 配置；缺失时延迟到存在真实来源再明确失败。"""

        try:
            return self._runtime.freeze(task_id).host_factory
        except Exception as exc:  # noqa: BLE001 - 单项任务失败不阻断调度器。
            self._log_unavailable(task_id, exc)
            return self._unavailable_host_factory(str(exc))

    @staticmethod
    def _unavailable_host_factory(message: str) -> HostFactory:
        """创建拒绝隐式回退到全局 Claude 配置的 host 工厂。"""

        def create_unavailable_host(
            _source_id: object,
            _workdir: Path,
        ) -> Any:
            """在管线确实需要模型时抛出安全配置错误。"""

            raise BackgroundRuntimeError(message)

        return create_unavailable_host

    @staticmethod
    def _log_unavailable(task_id: TaskId, error: Exception) -> None:
        """记录不含启动 secret 的任务不可用原因。"""

        logger.warning(
            "[background] %s runtime unavailable: %s",
            task_id.value,
            error,
        )


@dataclass(frozen=True)
class BackgroundSchedulers:
    """保存三类调度器，供应用 DrainCoordinator 分别管理。"""

    memory: MemoryReviewScheduler
    profile: ProfileDistillScheduler
    tidy: TidyScheduler

    async def start(self) -> None:
        """依次启动调度器；中途失败时关闭本次已经启动的组件。"""

        started: list[Any] = []
        try:
            for scheduler in (self.memory, self.profile, self.tidy):
                await scheduler.start()
                started.append(scheduler)
        except BaseException:
            for scheduler in reversed(started):
                await scheduler.stop()
            raise


def build_background_schedulers(
    runtime: BackgroundRuntimeManager,
    memory_root: Path,
    *,
    proxy_base_url: str,
    settings_path: Path | str | None,
    resource_registry: ResourceRegistry,
) -> BackgroundSchedulers:
    """按应用依赖构造调度器，任务配置仍延迟到每次执行时读取。

    Args:
        runtime: Trowel 托管后台 runtime 管理器。
        memory_root: 五项任务共享的 Memory 根目录。
        proxy_base_url: 兼容旧 Profile 处理函数签名的本地代理地址。
        settings_path: 兼容旧 Profile 处理函数签名的 Claude settings 路径。
        resource_registry: 应用资源账本。
    """

    # 通过模块属性读取，使测试和运行期配置替换不会被导入时缓存的函数引用绕过。
    from trowel_py.memory.daily_review import scheduler as review_scheduler
    from trowel_py.profile.distill import scheduler as distill_scheduler

    dispatcher = BackgroundTaskDispatcher(runtime, memory_root)
    return BackgroundSchedulers(
        memory=MemoryReviewScheduler(
            review_scheduler.load_review_config(),
            memory_root,
            dispatch_fn=dispatcher.dispatch_memory_review,
            resource_registry=resource_registry,
        ),
        profile=ProfileDistillScheduler(
            distill_scheduler.load_distill_config(),
            memory_root,
            proxy_base_url,
            settings_path,
            dispatch_fn=dispatcher.dispatch_profile_distill,
            resource_registry=resource_registry,
        ),
        tidy=TidyScheduler(
            memory_root,
            dispatcher.weekly_provider,
            monthly_provider_factory=dispatcher.monthly_provider,
        ),
    )
