"""为配置领域测试创建隔离仓储和真实模型列表样例。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from trowel_py.configuration.catalog import FetchedCatalog
from trowel_py.configuration.repository import ConfigurationRepository
from trowel_py.configuration.service import ConfigurationService
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations


@dataclass
class FakeCatalogFetcher:
    """返回测试指定的真实协议形状，并允许在响应前触发并发动作。

    Attributes:
        result: 模型列表服务应返回的去身份化结果。
        before_return: 返回前执行的动作，用于模拟请求飞行期间发生的配置更新。
    """

    result: FetchedCatalog
    before_return: Callable[[], None] | None = None

    async def fetch(
        self,
        *,
        base_url: str,
        api_key: str,
        models_url: str | None,
    ) -> FetchedCatalog:
        """核对调用拿到了内部 secret，再返回指定结果。"""

        assert base_url.startswith("https://")
        assert api_key
        if self.before_return is not None:
            self.before_return()
        return self.result


def build_service(
    *,
    fetcher: FakeCatalogFetcher | None = None,
) -> tuple[ConfigurationService, ConfigurationRepository]:
    """在内存主库上运行真实 migration 并返回配置服务。"""

    connection = create_db(":memory:")
    run_migrations(connection)
    repository = ConfigurationRepository(connection)
    service = ConfigurationService(repository, catalog_fetcher=fetcher)
    return service, repository
