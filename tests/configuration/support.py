"""为配置领域测试创建隔离仓储和真实模型列表样例。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from trowel_py.configuration.catalog import FetchedCatalog
from trowel_py.configuration.claude_home import ClaudeConnectionHomeStore
from trowel_py.configuration.codex_home import CodexConnectionHomeStore
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


class IsolatedClaudeConnectionHomeStore(ClaudeConnectionHomeStore):
    """在对象生命周期结束时自动清理测试连接家和全局来源。"""

    def __init__(self) -> None:
        """创建一组不接触用户真实 Claude 配置的临时目录。"""

        self._temporary_root = TemporaryDirectory(prefix="trowel-config-test-")
        root = Path(self._temporary_root.name)
        super().__init__(
            root / "claude-connections",
            global_home=root / "global-claude",
        )


class IsolatedCodexConnectionHomeStore(CodexConnectionHomeStore):
    """在对象生命周期结束时自动清理测试 Codex 连接家与全局来源。"""

    def __init__(self, root: Path | None = None) -> None:
        """创建不接触用户真实配置的来源，并允许测试指定托管根。

        Args:
            root: 测试需要断言的 Codex 托管根；None 时也放进临时目录。
        """

        self._temporary_root = TemporaryDirectory(prefix="trowel-codex-config-test-")
        source_root = Path(self._temporary_root.name)
        super().__init__(
            root or source_root / "codex-accounts",
            global_codex_home=source_root / "global-codex",
            global_agents_home=source_root / "global-agents",
        )


def build_service(
    *,
    fetcher: FakeCatalogFetcher | None = None,
    official_account_root: Path | None = None,
    claude_homes: ClaudeConnectionHomeStore | None = None,
    codex_homes: CodexConnectionHomeStore | None = None,
) -> tuple[ConfigurationService, ConfigurationRepository]:
    """在内存主库上运行真实 migration，并注入可隔离的 runtime 目录。"""

    connection = create_db(":memory:")
    run_migrations(connection)
    repository = ConfigurationRepository(connection)
    isolated_claude_homes = claude_homes or IsolatedClaudeConnectionHomeStore()
    isolated_codex_homes = codex_homes or IsolatedCodexConnectionHomeStore(
        official_account_root
    )
    service = ConfigurationService(
        repository,
        catalog_fetcher=fetcher,
        official_account_root=official_account_root,
        claude_homes=isolated_claude_homes,
        codex_homes=isolated_codex_homes,
    )
    return service, repository
