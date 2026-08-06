"""按冻结连接身份持有多个相互隔离的 Codex app-server manager。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trowel_py.codex_host.manager import CodexHostManager
from trowel_py.codex_host.transport import AppServerClient
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
from trowel_py.resource_lifecycle.registry import ResourceRegistry

ManagerFactory = Callable[[RuntimeLaunchConfiguration], CodexHostManager]
PrewarmClientFactory = Callable[[], AppServerClient]


class CodexManagerPool:
    """按连接 identity 路由 session，并在应用退出时统一关闭 manager。

    manager 空闲后保持热状态，不启动后台计时器；池容量由设置域允许的连接数上界
    约束。单个 manager 的失败和重连状态不会广播到其他连接。
    """

    def __init__(
        self,
        *,
        shared_state_root: Path,
        legacy_manager: CodexHostManager | None = None,
        manager_factory: ManagerFactory | None = None,
        prewarm_client_factory: PrewarmClientFactory | None = None,
        resource_registry: ResourceRegistry | None = None,
        max_managers: int = 25,
    ) -> None:
        """保存共享状态根、工厂和有界 manager 集合。

        Args:
            shared_state_root: 多个 app-server 共用的 thread 与 SQLite 状态目录。
            legacy_manager: 未绑定设置域连接的内部兼容 manager。
            manager_factory: 根据冻结连接构造 manager 的测试替换点。
            prewarm_client_factory: 串行初始化空状态库的 client 工厂。
            resource_registry: 多 manager 共用的应用资源账本。
            max_managers: 同时保留的连接身份 manager 上限。
        """

        self._shared_state_root = shared_state_root
        self._shared_state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._shared_state_root.chmod(0o700)
        self._resource_registry = resource_registry
        self._legacy = legacy_manager or CodexHostManager(
            resource_registry=resource_registry,
            resource_namespace="legacy",
        )
        self._manager_factory = manager_factory or self._build_manager
        self._prewarm_client_factory = (
            prewarm_client_factory or self._build_prewarm_client
        )
        self._max_managers = max_managers
        self._managers: dict[str, CodexHostManager] = {}
        self._releasing: set[str] = set()
        self._session_managers: dict[str, CodexHostManager] = {}
        self._prewarm_lock = asyncio.Lock()
        self._prewarmed = False

    @property
    def session_ids(self) -> tuple[str, ...]:
        """按登记顺序返回池内全部 Trowel 会话 ID。"""

        return tuple(self._session_managers)

    @property
    def manager_count(self) -> int:
        """返回已创建的设置域连接 manager 数量，不包含兼容 manager。"""

        return len(self._managers)

    def register(
        self,
        session: Any,
        *,
        launch: RuntimeLaunchConfiguration | None = None,
    ) -> None:
        """把 session 登记到冻结连接 manager；内部旧调用走兼容 manager。"""

        manager = self._legacy if launch is None else self._manager_for_launch(launch)
        manager.register(session)
        self._session_managers[session.session_id] = manager

    def unregister(self, session_id: str) -> Any | None:
        """从所属 manager 注销 session，但保留空闲 manager 到应用退出。"""

        manager = self._session_managers.pop(session_id, None)
        return manager.unregister(session_id) if manager is not None else None

    def get_session(self, session_id: str) -> Any | None:
        """按 Trowel 会话 ID 从所属 manager 读取 session。"""

        manager = self._session_managers.get(session_id)
        return manager.get_session(session_id) if manager is not None else None

    async def attach(self, session: Any, **kwargs: Any) -> Any:
        """预热共享状态后由所属 manager 恢复原生 thread。"""

        manager = self._require_manager(session.session_id)
        await self._ensure_prewarmed()
        return await manager.attach(session, **kwargs)

    async def send(self, session: Any, text: str, **kwargs: Any) -> str:
        """预热共享状态后由所属 manager 启动一轮输入。"""

        manager = self._require_manager(session.session_id)
        await self._ensure_prewarmed()
        return await manager.send(session, text, **kwargs)

    async def interrupt(self, session: Any) -> None:
        """只中断 session 所属 manager 中的当前轮次。"""

        await self._require_manager(session.session_id).interrupt(session)

    async def close_session(self, session: Any, **kwargs: Any) -> None:
        """只关闭 session 所属 manager 中的原生资源。"""

        await self._require_manager(session.session_id).close_session(session, **kwargs)

    def answer_request(self, session_id: str, request_id: str, decision: str) -> Any:
        """把审批答复路由到 session 所属 manager。"""

        return self._require_manager(session_id).answer_request(
            session_id, request_id, decision
        )

    def list_requests(self, session_id: str) -> tuple[Any, ...]:
        """读取 session 所属 manager 保存的待决审批。"""

        return self._require_manager(session_id).list_requests(session_id)

    async def list_models(self) -> list[dict[str, Any]]:
        """保留旧模型端点，使用兼容 manager 的原生 catalog。"""

        await self._ensure_prewarmed()
        return await self._legacy.list_models()

    async def list_models_for_launch(
        self, launch: RuntimeLaunchConfiguration
    ) -> list[dict[str, Any]]:
        """使用指定连接的独立 app-server 读取原生模型目录。

        Args:
            launch: 已冻结 provider、凭据、代理和连接身份的启动配置。

        Returns:
            该连接对应 Codex app-server 返回的标准化模型目录。
        """

        await self._ensure_prewarmed()
        return await self._manager_for_launch(launch).list_models()

    async def read_account_for_launch(
        self, launch: RuntimeLaunchConfiguration
    ) -> dict[str, str | None]:
        """读取一项 Official 连接独立账号槽位的脱敏状态。"""

        await self._ensure_prewarmed()
        return await self._manager_for_launch(launch).read_account()

    async def start_account_login_for_launch(
        self, launch: RuntimeLaunchConfiguration
    ) -> dict[str, str]:
        """为一项 Official 连接启动 Codex 原生 device-code 登录。"""

        await self._ensure_prewarmed()
        return await self._manager_for_launch(launch).start_account_login()

    async def release_launch(self, launch: RuntimeLaunchConfiguration) -> bool:
        """关闭并移除没有会话引用的连接 manager。

        Returns:
            manager 不存在或已释放时为 True；仍有冻结会话引用时为 False。
        """

        key = launch.pool_key
        if key in self._releasing:
            return False
        manager = self._managers.get(key)
        if manager is None:
            return True
        if manager in self._session_managers.values():
            return False
        self._releasing.add(key)
        try:
            await manager.close()
            if self._managers.get(key) is manager:
                self._managers.pop(key, None)
            return True
        finally:
            self._releasing.discard(key)

    async def list_commands(self) -> list[dict[str, Any]]:
        """读取当前 Codex CLI 版本对应的已验证命令表。"""

        await self._ensure_prewarmed()
        return await self._legacy.list_commands()

    async def get_goal(self, session: Any) -> dict[str, Any] | None:
        """从 session 所属连接读取 thread Goal。"""

        await self._ensure_prewarmed()
        return await self._require_manager(session.session_id).get_goal(session)

    async def set_goal(self, session: Any, **fields: Any) -> dict[str, Any]:
        """在 session 所属连接更新 thread Goal。"""

        await self._ensure_prewarmed()
        return await self._require_manager(session.session_id).set_goal(
            session, **fields
        )

    async def clear_goal(self, session: Any) -> bool:
        """在 session 所属连接清除 thread Goal。"""

        await self._ensure_prewarmed()
        return await self._require_manager(session.session_id).clear_goal(session)

    async def compact(self, session: Any, **kwargs: Any) -> None:
        """把上下文压缩请求路由到 session 所属连接。"""

        await self._ensure_prewarmed()
        await self._require_manager(session.session_id).compact(session, **kwargs)

    async def start_review(
        self, session: Any, target: dict[str, Any], **kwargs: Any
    ) -> dict[str, str]:
        """把原生审查请求路由到 session 所属连接。"""

        await self._ensure_prewarmed()
        return await self._require_manager(session.session_id).start_review(
            session, target, **kwargs
        )

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """合并当前已存在 manager 的历史列表并按更新时间截断。"""

        await self._ensure_prewarmed()
        managers = self._all_managers()
        results = await asyncio.gather(
            *(
                manager.list_threads(cwd=cwd, limit=limit, excluded_ids=excluded_ids)
                for manager in managers
            ),
            return_exceptions=True,
        )
        pages = [page for page in results if isinstance(page, list)]
        if not pages:
            failures = [
                result for result in results if isinstance(result, BaseException)
            ]
            if failures:
                raise failures[0]
            return []
        rows_by_id: dict[str, dict[str, Any]] = {}
        anonymous_rows: list[dict[str, Any]] = []
        for page in pages:
            for row in page:
                thread_id = row.get("id")
                if isinstance(thread_id, str) and thread_id:
                    current = rows_by_id.get(thread_id)
                    if current is None or str(row.get("updatedAt", "")) > str(
                        current.get("updatedAt", "")
                    ):
                        rows_by_id[thread_id] = row
                else:
                    anonymous_rows.append(row)
        rows = [*rows_by_id.values(), *anonymous_rows]
        rows.sort(key=lambda row: str(row.get("updatedAt", "")), reverse=True)
        return rows[:limit]

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """在当前 manager 集合中查找指定原生 thread。"""

        await self._ensure_prewarmed()
        last_error: BaseException | None = None
        for manager in self._all_managers():
            try:
                return await manager.read_thread(thread_id)
            except Exception as exc:  # noqa: BLE001 - 另一个连接可能持有该 thread。
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("no Codex manager is available")

    async def close(self) -> None:
        """并发关闭所有相互独立的 manager，单个失败不跳过其他连接。"""

        results = await asyncio.gather(
            *(manager.close() for manager in self._all_managers()),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise ExceptionGroup("Codex manager pool close failed", failures)

    def _manager_for_launch(
        self, launch: RuntimeLaunchConfiguration
    ) -> CodexHostManager:
        """读取或惰性创建连接 identity 对应的 manager。"""

        key = launch.pool_key
        if key in self._releasing:
            raise RuntimeError("Codex connection manager is being released")
        manager = self._managers.get(key)
        if manager is not None:
            return manager
        if len(self._managers) >= self._max_managers:
            raise RuntimeError("Codex connection manager pool is full")
        manager = self._manager_factory(launch)
        self._managers[key] = manager
        return manager

    def _require_manager(self, session_id: str) -> CodexHostManager:
        """返回 session 所属 manager；未知 session 立即报错。"""

        manager = self._session_managers.get(session_id)
        if manager is None:
            raise RuntimeError(f"Codex session {session_id} is not registered")
        return manager

    def _all_managers(self) -> tuple[CodexHostManager, ...]:
        """返回去重且顺序稳定的兼容 manager 与连接 manager。"""

        return (self._legacy, *self._managers.values())

    async def _ensure_prewarmed(self) -> None:
        """在任何 manager 并发启动前串行完成一次状态库 migration。"""

        if self._prewarmed:
            return
        async with self._prewarm_lock:
            if self._prewarmed:
                return
            client = self._prewarm_client_factory()
            try:
                await client.start()
            finally:
                await client.close()
            self._prewarmed = True

    def _build_prewarm_client(self) -> AppServerClient:
        """创建只负责初始化共享状态根的短命 app-server client。"""

        return AppServerClient(
            env={
                "CODEX_HOME": str(self._shared_state_root),
                "CODEX_SQLITE_HOME": str(self._shared_state_root),
            },
            process_controller=(
                self._resource_registry.process_controller
                if self._resource_registry is not None
                else None
            ),
        )

    def _build_manager(self, launch: RuntimeLaunchConfiguration) -> CodexHostManager:
        """根据冻结连接构造不共享 transport 状态的 manager。"""

        namespace = f"pool-{launch.pool_key[:12]}"
        return CodexHostManager(
            client_factory=lambda: AppServerClient(
                env=launch.codex_environment(shared_state_root=self._shared_state_root),
                config_overrides=launch.codex_overrides(),
                process_controller=(
                    self._resource_registry.process_controller
                    if self._resource_registry is not None
                    else None
                ),
            ),
            resource_registry=self._resource_registry,
            resource_namespace=namespace,
        )
