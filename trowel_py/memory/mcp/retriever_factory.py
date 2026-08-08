"""把设置域的 Memory 运行配置适配为在线检索器。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from trowel_py.application_paths import has_application_data_root_override
from trowel_py.configuration.errors import ConfigurationError
from trowel_py.configuration.models import TaskId
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
from trowel_py.configuration.text_provider import (
    TextProviderConfigurationError,
    anthropic_llm_config,
)
from trowel_py.llm.client import AnthropicProvider, LLMConfig, LLMProvider
from trowel_py.memory.eval import Retriever
from trowel_py.memory.retrievers import LLMRetriever


class MemoryRetrieverFactory(Protocol):
    """声明 Memory MCP 在一次 search 边界取得检索器的最小接口。"""

    def __call__(self) -> Retriever:
        """返回本次 search 使用的检索器。"""


TaskLaunchResolver = Callable[[TaskId], RuntimeLaunchConfiguration]
ProviderBuilder = Callable[[LLMConfig], LLMProvider]


class MemoryRetrieverConfigurationError(RuntimeError):
    """表示当前任务绑定不能提供在线 Dictionary 检索模型。"""


@dataclass(frozen=True)
class TaskBoundRetrieverFactory:
    """从 Memory 精炼任务绑定构造一次无全局回退的 LLM 检索器。

    现有两层 Dictionary 检索使用 Anthropic Messages 协议直接完成两个短请求，
    不需要启动完整 Agent。解析端口由 composition root 注入，便于以后增加其他
    检索 provider，而不让 MCP 处理器依赖设置仓储。

    Attributes:
        resolve_task_launch: 热读取并冻结任务启动配置的端口。
        provider_builder: 把协议配置转换成文本模型 provider 的工厂。
    """

    resolve_task_launch: TaskLaunchResolver
    provider_builder: ProviderBuilder = AnthropicProvider

    def __call__(self) -> Retriever:
        """解析 Memory 精炼绑定，并创建本次搜索使用的检索器。"""

        launch = self.resolve_task_launch(TaskId.MEMORY_REFINE)
        return LLMRetriever(self.provider_builder(_llm_config(launch)))


@dataclass(frozen=True)
class LazyRetriever:
    """把检索器创建延迟到真正存在 Dictionary 的搜索调用。"""

    factory: MemoryRetrieverFactory

    def __call__(
        self,
        query: str,
        *,
        corpus_dir: str,
        dictionary_path: str,
    ) -> list[str]:
        """按本次最新设置创建检索器，并原样转发搜索参数。"""

        return list(
            self.factory()(
                query,
                corpus_dir=corpus_dir,
                dictionary_path=dictionary_path,
            )
        )


def create_default_memory_retriever() -> Retriever:
    """从设置数据库创建在线检索器，旧 browser 布局兼容 config.toml。

    桌面环境只认设置域绑定，不会在绑定失效时退回全局配置。没有应用数据根覆盖
    的旧 browser/CLI 布局仍可在配置表尚未建立或任务尚未绑定时读取历史
    ``config.toml``，以保持原入口兼容。
    """

    try:
        return TaskBoundRetrieverFactory(_resolve_task_launch_from_database)()
    except ConfigurationError as exc:
        if has_application_data_root_override() or exc.code != "TASK_CONFIGURATION_REQUIRED":
            raise
    except sqlite3.OperationalError:
        if has_application_data_root_override():
            raise

    from trowel_py.config import load_llm_config

    return LLMRetriever(AnthropicProvider(load_llm_config()))


def _resolve_task_launch_from_database(
    task_id: TaskId,
) -> RuntimeLaunchConfiguration:
    """用短连接读取一次设置域任务绑定，并保证连接及时关闭。"""

    from trowel_py.configuration.repository import ConfigurationRepository
    from trowel_py.configuration.service import ConfigurationService
    from trowel_py.db.connection import create_db

    connection = create_db()
    try:
        service = ConfigurationService(ConfigurationRepository(connection))
        return service.resolve_task_launch(task_id)
    finally:
        connection.close()


def _llm_config(launch: RuntimeLaunchConfiguration) -> LLMConfig:
    """把可直接调用的 Anthropic 启动快照收窄为检索 provider 配置。"""

    try:
        return anthropic_llm_config(launch)
    except TextProviderConfigurationError as exc:
        raise MemoryRetrieverConfigurationError(
            f"Memory 在线检索配置不可用：{exc}"
        ) from exc
