"""管理共享 Codex app-server，并按 threadId 路由原生通知。

首个请求惰性启动 transport；意外 EOF 会进入 degraded，并向在途 turn 发出
host_exited 终态。transport 只需实现 AppServerClient 协议。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import Any, Callable, Mapping

from trowel_py.codex_host.catalog import parse_model_list_page
from trowel_py.codex_host.account import parse_account_read, parse_device_code_login
from trowel_py.codex_host.child_threads import ChildThreadRegistry
from trowel_py.codex_host.commands import command_roster
from trowel_py.codex_host.errors import (
    ProtocolViolationError,
    ServerRequestUnsupportedError,
)
from trowel_py.codex_host.events import (
    CodexEventType,
    HostStatusKind,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host import manager_params
from trowel_py.codex_host.session import CodexSession, ThreadBinding, TurnConflictError
from trowel_py.codex_host.pending_requests import (
    PendingRequest,
    PendingRequestKind,
    PendingRequestNotFoundError,
    PendingRequestOwnershipError,
    PendingRequestRegistry,
)
from trowel_py.codex_host.translator import CodexTranslator
from trowel_py.codex_host.transport import AppServerClient
from trowel_py.resource_lifecycle.models import OwnerScope, ProcessIdentity
from trowel_py.resource_lifecycle.processes import list_descendant_processes
from trowel_py.resource_lifecycle.registry import ResourceRegistry

_log = logging.getLogger(__name__)

# 首次请求可能包含认证检查，因此保留充足的握手时间。
_REQUEST_TIMEOUT_S = 60.0
_PENDING_REQUEST_TIMEOUT_S = 600.0

_COMMAND_APPROVAL_METHOD = "item/commandExecution/requestApproval"
_FILE_APPROVAL_METHOD = "item/fileChange/requestApproval"


class CodexHostManagerState(str, Enum):
    """manager 生命周期。

    除 ``ready`` 且 client 仍打开的情况外，``ensure_ready`` 都会发起新连接。

    Transitions::

        non-usable --ensure_ready--> starting --success--> ready
        starting --failure--> degraded
        ready --unexpected close--> degraded
        any --close--> closing --success--> stopped
    """

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    CLOSING = "closing"


@dataclass(frozen=True)
class OrphanDiagnostic:
    """无法路由的通知诊断。

    orphan 只记录而不抛出，避免阻塞消息总线，也绝不能进入其他会话。

    Attributes:
        method: 原生通知方法名。
        thread_id: 通知携带的 Codex thread ID；缺失时为 None。
        turn_id: 通知携带的 Codex turn ID；缺失时为 None。
        reason: 无法路由的稳定原因码。
    """

    method: str
    thread_id: str | None
    turn_id: str | None
    reason: str


ClientFactory = Callable[[], AppServerClient]
BeforeTurnStart = Callable[[CodexSession], None]
DescendantInventory = Callable[[int], tuple[ProcessIdentity, ...]]


def _is_missing_rollout_error(
    error: ProtocolViolationError,
    thread_id: str,
) -> bool:
    """识别 app-server 找不到未归档 rollout 时返回的精确错误。"""

    return _matches_rpc_error(
        error,
        code=-32600,
        message=f"no rollout found for thread id {thread_id}",
    )


def _is_missing_archived_rollout_error(
    error: ProtocolViolationError,
    thread_id: str,
) -> bool:
    """识别 app-server 找不到已归档 rollout 时返回的精确错误。"""

    return _matches_rpc_error(
        error,
        code=-32600,
        message=f"no archived rollout found for thread id {thread_id}",
    )


def _is_unmaterialized_thread_error(
    error: ProtocolViolationError,
    thread_id: str,
) -> bool:
    """识别首条用户消息前 thread 尚未生成 rollout 的精确错误。"""

    return _matches_rpc_error(
        error,
        code=-32600,
        message=(
            f"thread {thread_id} is not materialized yet; includeTurns is "
            "unavailable before first user message"
        ),
    )


def _is_agent_jobs_schema_delete_error(
    error: ProtocolViolationError,
    thread_id: str,
) -> bool:
    """识别 Codex 0.144 删除空 thread 时命中的缺表错误。"""

    return _matches_rpc_error(
        error,
        code=-32603,
        message=(
            f"failed to delete app-server state for {thread_id}: error returned "
            "from database: (code: 1) no such table: agent_jobs"
        ),
    )


def _matches_rpc_error(
    error: ProtocolViolationError,
    *,
    code: int,
    message: str,
) -> bool:
    """按错误码和完整消息匹配当前请求的 app-server 响应。

    Args:
        error: transport 保存了原始响应的协议错误。
        code: 当前已实证的 JSON-RPC 错误码。
        message: 必须完整一致且包含当前 thread 身份的错误消息。

    Returns:
        原始响应同时匹配错误码和完整消息时为 True。
    """

    payload = error.payload
    if not isinstance(payload, Mapping):
        return False
    response_error = payload.get("error")
    if not isinstance(response_error, Mapping):
        return False
    return (
        response_error.get("code") == code and response_error.get("message") == message
    )


class CodexHostManager:
    """持有共享 transport 与 thread→session 路由表。"""

    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        translator: CodexTranslator | None = None,
        pending_request_timeout_s: float = _PENDING_REQUEST_TIMEOUT_S,
        resource_registry: ResourceRegistry | None = None,
        descendant_inventory: DescendantInventory = list_descendant_processes,
        descendant_poll_interval_s: float = 0.25,
        resource_namespace: str = "codex",
    ) -> None:
        """初始化共享连接、会话路由和待决请求登记表。

        Args:
            client_factory: app-server client 工厂；省略时创建真实 ``AppServerClient``。
            translator: 原生通知翻译器；省略时创建无状态实例。
            pending_request_timeout_s: 命令审批等待答复的秒数。
            resource_registry: 登记 app-server 连接、thread 和 turn handle 的应用账本。
            descendant_inventory: 从进程表读取 app-server 后代身份的函数。
            descendant_poll_interval_s: 两次后代进程盘点之间的秒数。
            resource_namespace: 多 manager 共用资源账本时使用的稳定隔离前缀。
        """

        self._client_factory: ClientFactory = (
            client_factory or self._default_client_factory
        )
        self._translator: CodexTranslator = translator or CodexTranslator()
        self._client: AppServerClient | None = None
        self._state: CodexHostManagerState = CodexHostManagerState.STOPPED
        self._sessions: dict[str, CodexSession] = {}
        self._thread_to_session: dict[str, CodexSession] = {}
        self._child_threads = ChildThreadRegistry()
        # 只记录当前连接已加载原生 thread 的本地 session；thread 独占另由
        # _thread_to_session 保证，重连后 attachment 必须重建。
        self._attached_session_ids: set[str] = set()
        self._orphans: list[OrphanDiagnostic] = []
        self._ready_lock: asyncio.Lock = asyncio.Lock()
        self._eof_watcher: asyncio.Task[None] | None = None
        self._pending_requests = PendingRequestRegistry()
        self._pending_request_timeout_s = pending_request_timeout_s
        self._connection_generation = 0
        self._active_generation = 0
        self._resource_registry = resource_registry
        self._descendant_inventory = descendant_inventory
        self._descendant_poll_interval_s = max(descendant_poll_interval_s, 0.01)
        self._descendant_monitor_task: asyncio.Task[None] | None = None
        self._descendant_resource_ids: dict[tuple[int, int, str], str] = {}
        self._connection_resource_ids: dict[int, str] = {}
        self._thread_resource_ids: dict[tuple[str, int], str] = {}
        self._turn_resource_ids: dict[tuple[str, str], str] = {}
        self._resource_namespace = resource_namespace
        self._account_login_status: dict[str, str | None] | None = None
        self._account_login_completions: dict[str, dict[str, str | None]] = {}

    @property
    def state(self) -> CodexHostManagerState:
        """返回共享 Codex 进程的当前生命周期状态。"""

        return self._state

    @property
    def client(self) -> AppServerClient | None:
        """返回当前 Codex 连接；尚未启动或连接已失效时为空。"""

        return self._client

    @property
    def orphans(self) -> list[OrphanDiagnostic]:
        """返回无法归属到会话的通知诊断副本。"""

        return list(self._orphans)

    @property
    def translator(self) -> CodexTranslator:
        """返回当前使用的 Codex 事件翻译器。"""

        return self._translator

    @property
    def connection_generation(self) -> int:
        """返回最近一次连接启动尝试的代际编号；尚未尝试时为 0。"""

        return self._active_generation

    def register(self, session: CodexSession) -> None:
        """登记本地 session；同 ID 会替换登记项，但不会改写已有 thread 路由。"""

        self._sessions[session.session_id] = session

    def get_session(self, session_id: str) -> CodexSession | None:
        """按 Trowel 会话 ID 查找 Codex 会话。"""

        return self._sessions.get(session_id)

    @property
    def session_ids(self) -> tuple[str, ...]:
        """按注册顺序返回全部 Trowel 会话 ID。"""

        return tuple(self._sessions.keys())

    def unregister(self, session_id: str) -> CodexSession | None:
        """注销本地 session、关闭其 pending request 并移除路由，不删除原生 thread。"""

        session = self._sessions.pop(session_id, None)
        for request in self._pending_requests.close_session(session_id):
            if session is not None:
                self._emit_request_event(session, request)
        self._attached_session_ids.discard(session_id)
        if session is not None and session.binding is not None:
            self._thread_to_session.pop(session.binding.thread_id, None)
        for thread_id in self._child_threads.remove_session(session) if session else ():
            self._thread_to_session.pop(thread_id, None)
        return session

    def session_for_thread(self, thread_id: str) -> CodexSession | None:
        """查找当前负责指定 Codex 线程的 Trowel 会话。"""

        return self._thread_to_session.get(thread_id)

    def _require_registered(self, session: CodexSession) -> None:
        """拒绝跨 await 期间已被删除或替换的 session。"""

        if self._sessions.get(session.session_id) is not session:
            raise TurnConflictError(
                f"session {session.session_id} is no longer registered"
            )

    async def ensure_ready(self) -> AppServerClient:
        """串行化惰性启动；每次成功建连（含首次）都广播 READY。"""

        async with self._ready_lock:
            if (
                self._state is CodexHostManagerState.READY
                and self._client is not None
                and not self._client.closed
            ):
                return self._client
            self._state = CodexHostManagerState.STARTING
            # binding 跨连接保留，app-server 内存中的 attachment 不保留。
            self._attached_session_ids.clear()
            client = self._client_factory()
            self._connection_generation += 1
            generation = self._connection_generation
            self._active_generation = generation
            client.register_server_request_handler(
                _COMMAND_APPROVAL_METHOD,
                partial(self._handle_server_request, generation),
            )
            client.register_server_request_handler(
                _FILE_APPROVAL_METHOD,
                partial(self._handle_server_request, generation),
            )
            client.register_unknown_server_request_handler(
                partial(self._handle_unknown_server_request, generation)
            )
            # 握手前先安装新 identity，上一代迟到的 EOF 才不会降级新连接。
            self._client = client
            try:
                await client.start()
                self._register_connection_resource(client, generation)
                self._start_descendant_monitor(client, generation)
            except BaseException:
                if self._client is client:
                    self._client = None
                    self._state = CodexHostManagerState.DEGRADED
                raise
            client.add_notification_listener(
                partial(self._on_notification, generation=generation)
            )
            self._state = CodexHostManagerState.READY
            self._broadcast_host_status(HostStatusKind.READY, reason="ready")
            self._eof_watcher = asyncio.create_task(
                self._eof_watcher_loop(), name="codex-host-eof-watcher"
            )
            return client

    async def close(self) -> None:
        """依次关闭共享连接与监听任务，再清除本次连接的挂载状态。

        client 关闭异常会直接向上传播，此时后续清理不会执行，状态保持 ``closing``。
        """

        self._state = CodexHostManagerState.CLOSING
        self._close_generation_requests(
            self._active_generation, reason="app-server manager closed"
        )
        client = self._client
        watcher = self._eof_watcher
        self._eof_watcher = None
        if client is not None:
            connection_id = self._connection_id(self._active_generation)
            await self._inventory_connection_descendants(
                client,
                self._active_generation,
            )
            await self._stop_descendant_monitor()
            if self._resource_registry is not None:
                self._resource_registry.mark_owner_closing(
                    OwnerScope.RUNTIME_CONNECTION,
                    runtime_connection_id=connection_id,
                )
            try:
                await client.close()
            except BaseException as exc:
                self._mark_connection_needs_reconcile(
                    self._active_generation,
                    f"Codex app-server close failed: {type(exc).__name__}",
                )
                raise
            if self._resource_registry is not None:
                process_report = await asyncio.to_thread(
                    self._resource_registry.reconcile_process_groups,
                    runtime_connection_id=connection_id,
                )
                if process_report.remaining or process_report.errors:
                    self._mark_connection_needs_reconcile(
                        self._active_generation,
                        "Codex descendant process groups need reconciliation",
                    )
                    raise RuntimeError(
                        "Codex descendant process groups need reconciliation"
                    )
                self._resource_registry.mark_owner_closed(
                    OwnerScope.RUNTIME_CONNECTION,
                    runtime_connection_id=connection_id,
                )
        else:
            await self._stop_descendant_monitor()
        if watcher is not None and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — 关闭流程只记录 watcher 异常
                _log.debug("eof watcher raised during close", exc_info=True)
        self._client = None
        self._attached_session_ids.clear()
        self._state = CodexHostManagerState.STOPPED

    async def list_models(self) -> list[dict[str, Any]]:
        """翻页返回全部可见模型，保留原生顺序与未知枚举值。"""

        client = await self.ensure_ready()
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = await client.request(
                "model/list", params, timeout=_REQUEST_TIMEOUT_S
            )
            page, cursor = parse_model_list_page(result)
            rows.extend(page)
            if cursor is None:
                return rows

    async def read_account(self) -> dict[str, str | None]:
        """读取当前 ``CODEX_HOME`` 的脱敏账号摘要，不主动刷新 token。"""

        client = await self.ensure_ready()
        result = await client.request(
            "account/read",
            {"refreshToken": False},
            timeout=_REQUEST_TIMEOUT_S,
        )
        account = parse_account_read(result)
        if self._account_login_status is not None:
            account.update(self._account_login_status)
        return account

    async def start_account_login(self) -> dict[str, str]:
        """启动由 Codex 自己持有和刷新凭据的 ChatGPT device-code 登录。"""

        client = await self.ensure_ready()
        result = await client.request(
            "account/login/start",
            {"type": "chatgptDeviceCode"},
            timeout=_REQUEST_TIMEOUT_S,
        )
        login = parse_device_code_login(result)
        self._account_login_status = self._account_login_completions.pop(
            login["login_id"],
            {
                "login_id": login["login_id"],
                "login_status": "pending",
                "login_error": None,
            },
        )
        return login

    async def list_commands(self) -> list[dict[str, Any]]:
        """按当前已连接的 CLI 版本返回经过验证的命令能力。"""

        client = await self.ensure_ready()
        version = str(client.version) if client.version is not None else None
        return command_roster(version)

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """按更新时间列出指定 cwd 的默认交互 thread。

        不读取私有 rollout。已确认属于 Trowel 委派子会话的 thread 在原生分页期间
        排除，因此返回数量和后续合并分页不会被内部会话占用。

        Args:
            cwd: 只读取该工作目录下的 Codex thread。
            limit: 最多返回的非排除 thread 数。
            excluded_ids: 已确认属于 Trowel 委派子会话的 Codex thread ID。
        """

        if limit <= 0:
            return []
        client = await self.ensure_ready()
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while len(rows) < limit:
            params: dict[str, Any] = {
                "cwd": cwd,
                "limit": limit - len(rows),
                "sortKey": "updated_at",
                "sortDirection": "desc",
            }
            if cursor is not None:
                params["cursor"] = cursor
            result = await client.request(
                "thread/list", params, timeout=_REQUEST_TIMEOUT_S
            )
            data = result.get("data") if isinstance(result, Mapping) else None
            if not isinstance(data, list) or not all(
                isinstance(row, Mapping) for row in data
            ):
                raise ProtocolViolationError("thread/list result.data is not an array")
            for row in data:
                thread_id = row.get("id")
                if isinstance(thread_id, str) and thread_id in excluded_ids:
                    continue
                rows.append(dict(row))
                if len(rows) >= limit:
                    break

            next_cursor = result.get("nextCursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise ProtocolViolationError(
                    "thread/list nextCursor is not a string or null"
                )
            if next_cursor is None or len(rows) >= limit:
                return rows
            if next_cursor in seen_cursors:
                raise ProtocolViolationError("thread/list returned a repeated cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return rows

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """通过公共 ``thread/read`` 取得含 turns 的 transcript。"""

        client = await self.ensure_ready()
        result = await client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
            timeout=_REQUEST_TIMEOUT_S,
        )
        thread = result.get("thread") if isinstance(result, Mapping) else None
        if not isinstance(thread, Mapping):
            raise ProtocolViolationError("thread/read result.thread is not an object")
        return dict(thread)

    async def get_goal(self, session: CodexSession) -> dict[str, Any] | None:
        """读取当前会话绑定线程的目标。"""

        binding = await self.attach(session)
        client = await self.ensure_ready()
        result = await client.request(
            "thread/goal/get",
            {"threadId": binding.thread_id},
            timeout=_REQUEST_TIMEOUT_S,
        )
        goal = result.get("goal") if isinstance(result, Mapping) else None
        if goal is None:
            return None
        if not isinstance(goal, Mapping):
            raise ProtocolViolationError("thread/goal/get result.goal is not an object")
        return dict(goal)

    async def set_goal(
        self,
        session: CodexSession,
        *,
        objective: str | None = None,
        status: str | None = None,
        token_budget: int | None = None,
        token_budget_supplied: bool = False,
    ) -> dict[str, Any]:
        """更新当前会话绑定线程的目标字段并返回完整目标。

        Args:
            session: 目标所属的 Trowel 会话。
            objective: 新目标正文；None 表示不更新。
            status: 新目标状态；None 表示不更新。
            token_budget: 新 token 预算；可在显式提供时传 None 清空预算。
            token_budget_supplied: 是否将 ``token_budget`` 写入请求，用于区分省略字段和
                显式清空。

        Returns:
            app-server 更新后的完整 goal 对象。
        """

        binding = await self.attach(session)
        client = await self.ensure_ready()
        params: dict[str, Any] = {"threadId": binding.thread_id}
        if objective is not None:
            params["objective"] = objective
        if status is not None:
            params["status"] = status
        if token_budget_supplied:
            params["tokenBudget"] = token_budget
        result = await client.request(
            "thread/goal/set", params, timeout=_REQUEST_TIMEOUT_S
        )
        goal = result.get("goal") if isinstance(result, Mapping) else None
        if not isinstance(goal, Mapping):
            raise ProtocolViolationError("thread/goal/set result.goal is not an object")
        return dict(goal)

    async def clear_goal(self, session: CodexSession) -> bool:
        """清除当前会话绑定线程的目标。"""

        binding = await self.attach(session)
        client = await self.ensure_ready()
        result = await client.request(
            "thread/goal/clear",
            {"threadId": binding.thread_id},
            timeout=_REQUEST_TIMEOUT_S,
        )
        cleared = result.get("cleared") if isinstance(result, Mapping) else None
        if not isinstance(cleared, bool):
            raise ProtocolViolationError(
                "thread/goal/clear result.cleared is not boolean"
            )
        return cleared

    async def compact(
        self,
        session: CodexSession,
        *,
        before_start: BeforeTurnStart | None = None,
    ) -> None:
        """在空闲 thread 上启动原生压缩，并与 turn 启动共享会话预留。

        ``before_start`` 在 thread 挂载后、请求压缩前同步执行；回调或请求失败都会
        释放会话预留。
        """

        self._require_registered(session)
        session.begin_send(autonomous=True, memory_eligible=False)
        try:
            binding = await self.attach(session)
            if before_start is not None:
                before_start(session)
            client = await self.ensure_ready()
            await client.request(
                "thread/compact/start",
                {"threadId": binding.thread_id},
                timeout=_REQUEST_TIMEOUT_S,
            )
        except BaseException:
            session.abort_send()
            raise

    async def start_review(
        self,
        session: CodexSession,
        target: Mapping[str, Any],
        *,
        before_start: BeforeTurnStart | None = None,
    ) -> dict[str, str]:
        """启动 inline 原生 review，并登记不含 USER 事件的自主 turn。

        review 必须复用当前 thread；返回值同时包含确认后的 thread ID 和 turn ID。
        ``before_start`` 在 thread 挂载后、原生 review 启动前同步执行。
        """

        self._require_registered(session)
        session.begin_send(memory_eligible=False)
        try:
            binding = await self.attach(session)
            if before_start is not None:
                before_start(session)
            client = await self.ensure_ready()
            result = await client.request(
                "review/start",
                {
                    "threadId": binding.thread_id,
                    "target": dict(target),
                    "delivery": "inline",
                },
                timeout=_REQUEST_TIMEOUT_S,
            )
            turn_id = _extract_turn_id(result)
            review_thread_id = (
                result.get("reviewThreadId") if isinstance(result, Mapping) else None
            )
            if not isinstance(review_thread_id, str) or not review_thread_id:
                raise ProtocolViolationError(
                    "review/start response has no reviewThreadId",
                    payload=dict(result),
                )
            if review_thread_id != binding.thread_id:
                raise ProtocolViolationError(
                    "inline review/start returned a different reviewThreadId",
                    payload=dict(result),
                )
            session.record_autonomous_turn_started(turn_id)
            return {
                "review_thread_id": review_thread_id,
                "turn_id": turn_id,
            }
        except BaseException:
            session.abort_send()
            raise

    async def attach(
        self,
        session: CodexSession,
        *,
        before_commit: BeforeTurnStart | None = None,
    ) -> ThreadBinding:
        """在当前连接中 start 或 resume thread，但不启动 turn。

        同一会话在一个连接代际内只加载一次；已有 thread 不能同时归属其他会话。
        ``before_commit`` 在取得原生 thread ID 后、登记本地路由与资源前执行；失败时
        删除新空 thread，或 archive/unarchive 已有历史，使未持久化挂载不会变成孤儿。
        """

        self._require_registered(session)
        client = await self.ensure_ready()
        self._require_registered(session)
        if session.session_id in self._attached_session_ids:
            binding = session.binding
            if binding is None:
                raise ProtocolViolationError("attached session has no thread binding")
            if before_commit is not None:
                before_commit(session)
            return binding
        reserved_thread_id: str | None = None
        binding = session.binding
        attached: ThreadBinding | None = None
        commit_gate_failed = False
        try:
            if binding is not None:
                owner = self._thread_to_session.get(binding.thread_id)
                if owner is None:
                    self._thread_to_session[binding.thread_id] = session
                    reserved_thread_id = binding.thread_id
                elif owner is not session:
                    raise TurnConflictError(
                        f"thread {binding.thread_id} is already attached to "
                        f"session {owner.session_id}"
                    )
            if session.is_new_thread:
                result = await client.request(
                    "thread/start",
                    self._thread_start_params(session),
                    timeout=_REQUEST_TIMEOUT_S,
                )
            else:
                result = await client.request(
                    "thread/resume",
                    self._thread_resume_params(session),
                    timeout=_REQUEST_TIMEOUT_S,
                )
            self._require_registered(session)
            attached = session.attach_thread_binding(result)
            if before_commit is not None:
                try:
                    before_commit(session)
                except BaseException:
                    commit_gate_failed = True
                    raise
            session.emit_session_started_if_first()
            self._attached_session_ids.add(session.session_id)
            self._thread_to_session[attached.thread_id] = session
            self._register_thread_resource(session, attached.thread_id)
            return attached
        except BaseException:
            if commit_gate_failed and attached is not None:
                compensated = await self._compensate_uncommitted_attachment(
                    client,
                    session,
                    previous_binding=binding,
                    attached=attached,
                )
                if not compensated:
                    reserved_thread_id = None
            if (
                reserved_thread_id is not None
                and self._thread_to_session.get(reserved_thread_id) is session
            ):
                self._thread_to_session.pop(reserved_thread_id, None)
            raise

    async def _compensate_uncommitted_attachment(
        self,
        client: AppServerClient,
        session: CodexSession,
        *,
        previous_binding: ThreadBinding | None,
        attached: ThreadBinding,
    ) -> bool:
        """收口持久化门禁失败前已经由 app-server 创建或加载的 thread。

        新 thread 尚无用户 turn，可直接删除；恢复的历史 thread 通过 archive 后立即
        unarchive 卸载运行资源并保留历史。补偿自身失败时反向登记本地路由和资源，
        让后续关闭或重启对账仍能发现它。

        Args:
            client: 创建或恢复当前 thread 的 app-server 客户端。
            session: 挂载失败所属的 Trowel 会话。
            previous_binding: 挂载前的新会话空值或恢复占位绑定。
            attached: app-server 已返回的完整 thread 绑定。

        Returns:
            原生资源已确认收口并恢复旧绑定时为 True；补偿失败但资源已登记对账时为
            False。
        """

        try:
            if previous_binding is None:
                await client.request(
                    "thread/delete",
                    {"threadId": attached.thread_id},
                    timeout=_REQUEST_TIMEOUT_S,
                )
            else:
                await client.request(
                    "thread/archive",
                    {"threadId": attached.thread_id},
                    timeout=_REQUEST_TIMEOUT_S,
                )
                await client.request(
                    "thread/unarchive",
                    {"threadId": attached.thread_id},
                    timeout=_REQUEST_TIMEOUT_S,
                )
        except BaseException:  # noqa: BLE001 - 原异常优先，失败资源必须进入对账。
            self._attached_session_ids.add(session.session_id)
            self._thread_to_session[attached.thread_id] = session
            self._register_thread_resource(session, attached.thread_id)
            registry = self._resource_registry
            if registry is not None:
                summary = registry.owner_summary(
                    OwnerScope.SESSION,
                    agent_session_id=session.session_id,
                )
                registry.mark_owner_needs_reconcile(
                    OwnerScope.SESSION,
                    agent_session_id=session.session_id,
                    remaining_resource_count=max(summary.live_resource_count, 1),
                )
            _log.exception(
                "failed to compensate uncommitted Codex thread %s",
                attached.thread_id,
            )
            return False
        session.restore_thread_binding_after_failed_attach(previous_binding)
        return True

    async def send(
        self,
        session: CodexSession,
        text: str,
        *,
        before_turn_start: BeforeTurnStart | None = None,
        autonomous: bool = False,
        memory_eligible: bool = True,
    ) -> str:
        """执行一个 turn：确保连接、按需挂载 thread，再启动原生 turn。

        同一 session 的 thread 在每个连接代际只 start/resume 一次，后续
        turn 复用已加载的 thread。
        ``before_turn_start`` 在挂载后、原生工作前同步执行，确保持久化
        失败时不会留下失去追踪的 turn。返回原生 ``turn_id``。

        Args:
            session: 接收本轮输入的 Codex 会话。
            text: 发送给 Codex 的输入正文。
            before_turn_start: 原生 thread 挂载后、turn/start 前执行的同步持久化门禁。
            autonomous: 是否由 Trowel 内部事件启动；为 True 时不合成 USER 事件。
            memory_eligible: 自主 turn 是否允许进入会后 Memory 流程。

        Returns:
            Codex 接受本轮后返回的原生 turn ID。
        """

        self._require_registered(session)
        session.begin_send(
            autonomous=autonomous,
            memory_eligible=memory_eligible,
        )
        try:
            await self.attach(session, before_commit=before_turn_start)
            client = await self.ensure_ready()
            assert session.binding is not None
            self._require_registered(session)
            self._thread_to_session[session.binding.thread_id] = session
            self._require_registered(session)
            model, effort = session.next_turn_settings()
            approval, sandbox = session.next_turn_permission_override()
            turn_result = await client.request(
                "turn/start",
                self._turn_start_params(
                    session.binding.thread_id,
                    text,
                    model=model,
                    effort=effort,
                    approval=approval,
                    sandbox=sandbox,
                ),
                timeout=_REQUEST_TIMEOUT_S,
            )
            turn_id = _extract_turn_id(turn_result)
            try:
                self._require_registered(session)
            except TurnConflictError:
                # 仅处理 session 在 turn/start await 中被删除的竞态：拿到
                # turn_id 说明原生端已接收，必须中断已无消费者的隐形任务。
                try:
                    await client.request(
                        "turn/interrupt",
                        {"threadId": session.binding.thread_id, "turnId": turn_id},
                        timeout=_REQUEST_TIMEOUT_S,
                    )
                except Exception:  # noqa: BLE001 — 保留原注册状态错误
                    _log.warning(
                        "failed to interrupt turn %s for deleted session %s",
                        turn_id,
                        session.session_id,
                        exc_info=True,
                    )
                raise
            session.commit_turn_settings(model=model, effort=effort)
            self._register_turn_resource(session, turn_id)
            if autonomous:
                session.record_autonomous_turn_started(turn_id)
            else:
                session.record_turn_started(turn_id, text)
            return turn_id
        except BaseException:
            # 所有失败都需释放 _sending，成功路径由 record_turn_started 清除。
            session.abort_send()
            raise

    async def interrupt(self, session: CodexSession) -> None:
        """请求中断当前 turn；终态仍以原生 ``turn/completed.status`` 为准。"""

        binding = session.binding
        turn_id = session.current_turn_id
        if binding is None or turn_id is None:
            return
        client = await self.ensure_ready()
        await client.request(
            "turn/interrupt",
            {"threadId": binding.thread_id, "turnId": turn_id},
            timeout=_REQUEST_TIMEOUT_S,
        )
        # 原生中断确认后再取消审批；反序会让 turn 先结束，中断请求无法抵达。
        for request in self._pending_requests.resolve_turn_with_cancel(
            session.session_id, turn_id
        ):
            self._emit_request_event(session, request)

    async def close_session(
        self,
        session: CodexSession,
        *,
        preserve_history: bool,
        terminal_timeout_s: float = 2.0,
    ) -> None:
        """中断活动 turn，archive 原生资源，并按需恢复历史列表可见性。

        archive 是 Codex 0.144.0 实测能收敛 thread MCP 和命令进程的原生操作。
        用户持久 thread 在 archive 后立即 unarchive，但不 resume，因此历史重新
        可见且不会拉起新的 MCP；随后再核验资源归零。委派和其他内部 thread 保持
        archived。

        Args:
            session: 仍由当前 manager 登记的 Trowel Codex 会话。
            preserve_history: 是否在 archive 后 unarchive，使默认历史列表继续可见。
            terminal_timeout_s: interrupt 后等待原生 turn 终态的最长秒数；超时仍
                继续 archive，不能把 terminal 当作资源归零证明。

        Raises:
            TurnConflictError: session 已在关闭期间被替换或注销。
            CodexHostError: archive 或 unarchive 原生请求失败。
        """

        self._require_registered(session)
        binding = session.binding
        if binding is None:
            return
        if session.has_in_flight_turn:
            try:
                await self.interrupt(session)
            except Exception:  # noqa: BLE001 - archive 仍是更强的资源收敛操作。
                _log.warning(
                    "failed to interrupt Codex session %s before archive",
                    session.session_id,
                    exc_info=True,
                )
            await self._wait_for_session_terminal(
                session,
                timeout_s=terminal_timeout_s,
            )
        self._require_registered(session)
        client = await self.ensure_ready()
        archive_rollout_missing = False
        try:
            await client.request(
                "thread/archive",
                {"threadId": binding.thread_id},
                timeout=_REQUEST_TIMEOUT_S,
            )
        except ProtocolViolationError as exc:
            if session.config.ephemeral or not _is_missing_rollout_error(
                exc,
                binding.thread_id,
            ):
                raise
            archive_rollout_missing = True
            if preserve_history:
                _log.warning(
                    "Codex user thread is already archived or its rollout is missing; "
                    "attempting to restore history"
                )
            else:
                _log.warning(
                    "Codex internal thread rollout disappeared before close; "
                    "deleting the still-loaded runtime"
                )
                await client.request(
                    "thread/delete",
                    {"threadId": binding.thread_id},
                    timeout=_REQUEST_TIMEOUT_S,
                )
        if preserve_history:
            # unarchive 只恢复 notLoaded 历史文件，不会重启 thread 或 MCP。必须先做，
            # 否则资源核验失败会把 rollout 留在归档区，使下一次关闭误判为文件丢失。
            try:
                await client.request(
                    "thread/unarchive",
                    {"threadId": binding.thread_id},
                    timeout=_REQUEST_TIMEOUT_S,
                )
            except ProtocolViolationError as restore_error:
                if (
                    not archive_rollout_missing
                    or not _is_missing_archived_rollout_error(
                        restore_error,
                        binding.thread_id,
                    )
                ):
                    raise
                await self._delete_unmaterialized_user_thread(
                    client,
                    binding.thread_id,
                    restore_error=restore_error,
                )
        if self._resource_registry is not None:
            for owner_session_id, turn_id in tuple(self._turn_resource_ids):
                if owner_session_id == session.session_id:
                    self._mark_turn_resource_closed(owner_session_id, turn_id)
            await self._reconcile_session_process_groups(session.session_id)
            self._resource_registry.mark_owner_closed(
                OwnerScope.SESSION,
                agent_session_id=session.session_id,
            )

    async def _delete_unmaterialized_user_thread(
        self,
        client: AppServerClient,
        thread_id: str,
        *,
        restore_error: ProtocolViolationError,
    ) -> None:
        """只删除已证实没有首条用户消息和 rollout 的空 thread。

        ``thread/delete`` 会永久删除原生状态，因此不能根据当前进程里的
        ``has_started_turn`` 推断：重启后，已有历史的会话也没有进程内 turn。
        只有 archive、unarchive 都确认 rollout 缺失，且 includeTurns 返回“首条
        用户消息前尚未物化”时，才允许删除。

        Args:
            client: 当前共享 Codex app-server 连接。
            thread_id: 待核验并关闭的原生 Codex thread ID。
            restore_error: unarchive 找不到归档 rollout 的原始错误；核验不能证明
                thread 为空时重新抛出，确保 binding 保持可重试。

        Raises:
            ProtocolViolationError: thread 不是已实证的未物化空状态。
        """

        try:
            await client.request(
                "thread/read",
                {"threadId": thread_id, "includeTurns": True},
                timeout=_REQUEST_TIMEOUT_S,
            )
        except ProtocolViolationError as read_error:
            if not _is_unmaterialized_thread_error(read_error, thread_id):
                raise restore_error from read_error
        else:
            raise restore_error
        _log.warning(
            "Codex user thread has no first user message or rollout; "
            "deleting the empty native state"
        )
        try:
            await client.request(
                "thread/delete",
                {"threadId": thread_id},
                timeout=_REQUEST_TIMEOUT_S,
            )
        except ProtocolViolationError as delete_error:
            if not _is_agent_jobs_schema_delete_error(delete_error, thread_id):
                raise
            loaded = await client.request(
                "thread/loaded/list",
                {},
                timeout=_REQUEST_TIMEOUT_S,
            )
            loaded_thread_ids = (
                loaded.get("data") if isinstance(loaded, Mapping) else None
            )
            if (
                not isinstance(loaded_thread_ids, list)
                or not all(isinstance(item, str) for item in loaded_thread_ids)
                or thread_id in loaded_thread_ids
            ):
                raise delete_error
            _log.warning(
                "Codex 0.144 could not remove empty thread metadata because its "
                "state database lacks agent_jobs; the native thread is confirmed "
                "unloaded"
            )

    async def _reconcile_session_process_groups(self, session_id: str) -> None:
        """核验指定 Codex session 已登记的进程组全部退出。

        Args:
            session_id: 资源账本使用的 Trowel 会话 ID。

        Raises:
            RuntimeError: 仍有进程组存活或进程身份无法安全核验。
        """

        registry = self._resource_registry
        if registry is None:
            return
        process_report = await asyncio.to_thread(
            registry.reconcile_process_groups,
            agent_session_id=session_id,
        )
        if process_report.remaining or process_report.errors:
            raise RuntimeError("Codex session process groups need reconciliation")

    async def _wait_for_session_terminal(
        self,
        session: CodexSession,
        *,
        timeout_s: float,
    ) -> bool:
        """有界等待 CodexSession 接收原生 terminal 并清除在途状态。

        Args:
            session: interrupt 后等待状态变化的会话。
            timeout_s: 最长等待秒数；小于等于 0 时只检查一次。

        Returns:
            在截止时间前观察到无在途 turn 时为 True，超时时为 False。
        """

        deadline = asyncio.get_running_loop().time() + max(timeout_s, 0.0)
        while session.has_in_flight_turn:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.02, remaining))
        return True

    def answer_request(
        self, session_id: str, request_id: str, decision: str
    ) -> PendingRequest:
        """校验会话归属和决策值后，一次性解决待处理审批。"""

        request = self._pending_requests.resolve(session_id, request_id, decision)
        session = self._sessions.get(session_id)
        if session is not None:
            self._emit_request_event(session, request)
        return request

    def decline_request(self, session_id: str, request_id: str) -> PendingRequest:
        """为无人交互的内部会话立即自动拒绝待处理审批。

        Args:
            session_id: 审批所属 Trowel 会话 ID。
            request_id: 待处理请求 ID。

        Returns:
            已标记自动拒绝并完成原生响应 Future 的请求。

        Raises:
            PendingRequestOwnershipError: 请求属于另一会话。
        """

        request = self._pending_requests.get(request_id)
        if request is None:
            raise PendingRequestNotFoundError(request_id)
        if request.session_id != session_id:
            raise PendingRequestOwnershipError(request_id)
        request = self._pending_requests.resolve_automatically(
            request_id,
            "decline",
            reason="internal discussion sessions do not accept approvals",
        )
        session = self._sessions.get(session_id)
        if session is not None:
            self._emit_request_event(session, request)
        return request

    def list_requests(self, session_id: str) -> tuple[PendingRequest, ...]:
        """返回指定会话保留的全部待决请求记录。"""

        return self._pending_requests.list_for_session(session_id)

    async def _handle_server_request(
        self,
        generation: int,
        native_request_id: Any,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """登记已验证归属的审批，并等待一次性答复。

        所有 ``fileChange`` 审批当前都立即返回 decline；该上游请求结构没有提供路径、
        diff 或可选决策，无法交给用户判断。命令审批等待用户答复，超时后由登记表
        生成拒绝结果。
        """

        session = self._request_session(generation, method, native_request_id, params)
        kind = (
            PendingRequestKind.COMMAND_APPROVAL
            if method == _COMMAND_APPROVAL_METHOD
            else PendingRequestKind.FILE_APPROVAL
        )
        request = self._pending_requests.create(
            native_request_id=native_request_id,
            generation=generation,
            session_id=session.session_id,
            kind=kind,
            params=params,
        )
        if kind is PendingRequestKind.FILE_APPROVAL:
            self._pending_requests.resolve_automatically(
                request.request_id,
                "decline",
                reason="request omitted path, diff, and available decisions",
            )
            self._emit_request_event(session, request)
            return await request.response

        self._emit_request_event(session, request)
        try:
            return await asyncio.wait_for(
                asyncio.shield(request.response),
                timeout=self._pending_request_timeout_s,
            )
        except asyncio.TimeoutError:
            expired = self._pending_requests.expire(request.request_id)
            self._emit_request_event(session, expired)
            return await expired.response

    async def _handle_unknown_server_request(
        self,
        generation: int,
        native_request_id: Any,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """若能解析归属则发送 unsupported 事件，随后抛出不支持请求错误。"""

        try:
            session = self._request_session(
                generation, method, native_request_id, params
            )
        except ServerRequestUnsupportedError:
            raise
        request = self._pending_requests.create(
            native_request_id=native_request_id,
            generation=generation,
            session_id=session.session_id,
            kind=PendingRequestKind.UNKNOWN,
            params=params,
        )
        self._pending_requests.resolve_automatically(
            request.request_id,
            "unsupported",
            reason=f"unsupported server request method {method}",
        )
        self._emit_request_event(session, request)
        raise ServerRequestUnsupportedError(method, native_request_id)

    def _request_session(
        self,
        generation: int,
        method: str,
        native_request_id: Any,
        params: Mapping[str, Any],
    ) -> CodexSession:
        """只在当前连接代际内解析请求归属。"""

        if generation != self._active_generation:
            raise ServerRequestUnsupportedError(method, native_request_id)
        thread_id = _extract_thread_id(params)
        session = self._thread_to_session.get(thread_id or "")
        if session is None:
            raise ServerRequestUnsupportedError(method, native_request_id)
        return session

    @staticmethod
    def _emit_request_event(session: CodexSession, request: PendingRequest) -> None:
        """将待决请求的当前状态发到所属会话。"""

        session.emit_translated(
            TranslatedItem(
                type=CodexEventType.APPROVAL_REQUEST,
                thread_id=request.thread_id,
                turn_id=request.turn_id,
                item_id=request.item_id,
                payload=immutable_payload(**request.to_payload()),
            )
        )

    def _close_generation_requests(self, generation: int, *, reason: str) -> None:
        """关闭失效连接代际的全部审批，并通知原所属 session。"""

        if generation <= 0:
            return
        for request in self._pending_requests.close_generation(generation):
            request.resolution_reason = reason
            session = self._sessions.get(request.session_id)
            if session is not None:
                self._emit_request_event(session, request)

    def _on_notification(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        generation: int | None = None,
    ) -> None:
        """在当前连接代际内同步路由通知；下游入队均为非阻塞操作。"""

        if generation is not None and generation != self._active_generation:
            return
        if method == "account/login/completed":
            login_id = params.get("loginId")
            success = params.get("success")
            error = params.get("error")
            if isinstance(login_id, str) and isinstance(success, bool):
                completion = {
                    "login_id": login_id,
                    "login_status": "completed" if success else "failed",
                    "login_error": error if isinstance(error, str) else None,
                }
                if (
                    self._account_login_status is not None
                    and self._account_login_status.get("login_id") == login_id
                ):
                    self._account_login_status = completion
                else:
                    self._account_login_completions = {login_id: completion}
            return
        if method in self._translator.ignored_methods:
            return  # 能力门控或回显，无需分发
        if method in self._translator.account_level_methods:
            self._dispatch_account_level(method, params)
            return
        thread_id = _extract_thread_id(params)
        if thread_id is None:
            self._record_orphan(
                method, None, _extract_turn_id_from_params(params), "no_thread_id"
            )
            return
        session = self._thread_to_session.get(thread_id)
        if session is None:
            self._record_orphan(
                method,
                thread_id,
                _extract_turn_id_from_params(params),
                "unknown_thread",
            )
            return
        if method == "turn/started":
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            if not isinstance(turn_id, str) or not turn_id:
                self._record_orphan(method, thread_id, None, "missing_turn_id")
                return
            try:
                if self._child_threads.is_child(thread_id):
                    session.emit_child_translated(
                        TranslatedItem(
                            type=CodexEventType.TURN_STARTED,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            payload=immutable_payload(
                                autonomous=True,
                                memory_eligible=False,
                            ),
                        )
                    )
                else:
                    session.record_native_turn_started(turn_id)
            except TurnConflictError as exc:
                _log.warning("native turn start rejected for %s: %s", thread_id, exc)
            return
        try:
            items = self._translator.translate(method, params)
        except ProtocolViolationError as exc:
            # 已映射协议发生漂移时向所属 session 报错，但不能杀死 reader。
            _log.warning("translator rejected %s: %s", method, exc)
            error_item = TranslatedItem(
                type=CodexEventType.ERROR,
                thread_id=thread_id,
                turn_id=_extract_turn_id_from_params(params),
                payload=immutable_payload(
                    kind="translator_error",
                    method=method,
                    message=str(exc),
                ),
            )
            if self._child_threads.is_child(thread_id):
                session.emit_child_translated(error_item)
            else:
                session.emit_translated(error_item)
            return
        if not items:
            # 非忽略方法未产出事件时留诊断，避免协议变化被静默丢弃。
            self._record_orphan(
                method,
                thread_id,
                _extract_turn_id_from_params(params),
                "unknown_method",
            )
            return
        for item in items:
            if item.type is CodexEventType.SUBAGENT_ACTIVITY:
                child_thread_id = item.payload.get("agent_thread_id")
                if isinstance(child_thread_id, str) and child_thread_id:
                    accepted = self._child_threads.register(
                        thread_id=child_thread_id,
                        parent_thread_id=thread_id,
                        session=session,
                    )
                    if not accepted:
                        self._record_orphan(
                            method,
                            child_thread_id,
                            item.turn_id,
                            "child_thread_conflict",
                        )
                        continue
                    self._thread_to_session[child_thread_id] = session
            if self._child_threads.is_child(thread_id):
                session.emit_child_translated(item)
            else:
                session.emit_translated(item)
            if item.turn_id is not None and not session.has_in_flight_turn:
                self._mark_turn_resource_closed(session.session_id, item.turn_id)

    def _dispatch_account_level(self, method: str, params: Mapping[str, Any]) -> None:
        """翻译无 ``threadId`` 的账户级通知，并广播给全部已注册 session。

        此类通知没有唯一归属；协议错误只记录日志，避免污染所有事件队列。
        """

        try:
            items = self._translator.translate(method, params)
        except ProtocolViolationError as exc:
            _log.warning("translator rejected account-level %s: %s", method, exc)
            return
        if not items:
            return
        for session in self._sessions.values():
            for item in items:
                session.emit_translated(item)

    def _record_orphan(
        self, method: str, thread_id: str | None, turn_id: str | None, reason: str
    ) -> None:
        """追加无法路由的诊断并写 debug 日志，不向任何会话发事件。"""

        diag = OrphanDiagnostic(
            method=method, thread_id=thread_id, turn_id=turn_id, reason=reason
        )
        self._orphans.append(diag)
        _log.debug(
            "codex orphan notification: method=%s thread=%s turn=%s reason=%s",
            method,
            thread_id,
            turn_id,
            reason,
        )

    async def _eof_watcher_loop(self) -> None:
        """等待 transport 关闭；主动关闭不广播 degraded。"""

        client = self._client
        if client is None:
            return
        try:
            await client.wait_closed()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 — 防御未声明的 transport 异常
            _log.debug("wait_closed raised", exc_info=True)
            return
        if self._state is CodexHostManagerState.CLOSING:
            return
        await self._on_unexpected_exit(client)

    async def _on_unexpected_exit(self, client: AppServerClient) -> None:
        """进入 degraded，关闭本代审批，并将本地在途 turn 标记为 host_exited。"""

        if client is not self._client:
            # 旧 watcher 可能晚于新连接返回，不能让陈旧 EOF 降级当前连接。
            _log.debug("ignoring stale codex host exit")
            return
        await self._stop_descendant_monitor()
        exit_code = client.last_exit_code
        stderr_tail = client.stderr_tail[:200]
        self._state = CodexHostManagerState.DEGRADED
        self._mark_connection_needs_reconcile(
            self._active_generation,
            "Codex app-server connection ended before process-tree verification",
        )
        self._client = None
        self._attached_session_ids.clear()
        self._eof_watcher = None
        self._close_generation_requests(
            self._active_generation, reason="app-server process exited"
        )
        reason = "app-server process exited unexpectedly"
        if stderr_tail:
            reason = f"{reason}; stderr={stderr_tail!r}"
        for session in self._sessions.values():
            # 在途包括 begin_send 到 record_turn_started 的窗口；二者都需
            # HOST_EXITED 释放终态和发送占位，空闲 session 只接收状态变化。
            if session.has_in_flight_turn:
                session.mark_host_exited(reason, exit_code=exit_code)
            else:
                session.emit_host_status(HostStatusKind.DEGRADED, reason=reason)
        _log.warning("codex host degraded: %s (exit_code=%s)", reason, exit_code)

    def _start_descendant_monitor(
        self,
        client: AppServerClient,
        generation: int,
    ) -> None:
        """启动当前 app-server 代际的非阻塞后代进程盘点。"""

        if self._resource_registry is None or client.pid is None:
            return
        previous = self._descendant_monitor_task
        if previous is not None and not previous.done():
            previous.cancel()
        self._descendant_monitor_task = asyncio.create_task(
            self._descendant_monitor_loop(client, generation),
            name=f"codex-descendant-monitor:{generation}",
        )

    async def _stop_descendant_monitor(self) -> None:
        """取消并等待当前后代盘点任务，避免 owner closing 后迟到登记。"""

        task = self._descendant_monitor_task
        self._descendant_monitor_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _descendant_monitor_loop(
        self,
        client: AppServerClient,
        generation: int,
    ) -> None:
        """持续盘点共享连接后代，使 sidecar 硬崩前已有最新进程快照。"""

        try:
            while client is self._client and generation == self._active_generation:
                await self._inventory_connection_descendants(client, generation)
                await asyncio.sleep(self._descendant_poll_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 盘点失败不能杀死 app-server reader。
            _log.warning("Codex descendant process inventory failed", exc_info=True)

    async def _inventory_connection_descendants(
        self,
        client: AppServerClient,
        generation: int,
    ) -> None:
        """登记真实 PPID 链下的独立进程组，并关闭已经消失的盘点记录。"""

        registry = self._resource_registry
        if registry is None:
            return
        root_pid = getattr(client, "pid", None)
        if not isinstance(root_pid, int) or root_pid <= 0:
            return
        descendants = await asyncio.to_thread(self._descendant_inventory, root_pid)
        if client is not self._client or generation != self._active_generation:
            return
        root_identity = registry.process_controller.inspect(root_pid)
        if root_identity is None:
            return
        seen: set[tuple[int, int, str]] = set()
        for identity in descendants:
            if identity.process_group == root_identity.process_group:
                continue
            key = (generation, identity.pid, identity.start_identity)
            seen.add(key)
            if key in self._descendant_resource_ids:
                continue
            resource_id = self._resource_id(
                "descendant",
                f"{generation}:{identity.pid}:{identity.start_identity[:12]}",
            )
            try:
                registry.register_process_group(
                    resource_id=resource_id,
                    owner_scope=OwnerScope.RUNTIME_CONNECTION,
                    resource_kind="codex_descendant_process_group",
                    pid=identity.pid,
                    runtime="codex",
                    runtime_generation=generation,
                    runtime_connection_id=self._connection_id(generation),
                    parent_resource_id=self._connection_resource_ids.get(generation),
                )
            except RuntimeError:
                return
            except ValueError:
                continue
            self._descendant_resource_ids[key] = resource_id

        controller = registry.process_controller
        for key, resource_id in tuple(self._descendant_resource_ids.items()):
            if key[0] != generation or key in seen:
                continue
            record = registry.get(resource_id)
            assert record.pid is not None
            assert record.process_group is not None
            current = controller.inspect(record.pid)
            if current is not None and current.start_identity != key[2]:
                registry.mark_closed(resource_id)
                self._descendant_resource_ids.pop(key, None)
            elif current is None and not controller.group_alive(record.process_group):
                registry.mark_closed(resource_id)
                self._descendant_resource_ids.pop(key, None)

    def _broadcast_host_status(
        self, status: HostStatusKind, *, reason: str | None
    ) -> None:
        """向当前已注册会话广播进程状态；之后注册的会话不会补收。"""

        for session in self._sessions.values():
            session.emit_host_status(status, reason=reason)

    def _resource_id(self, kind: str, suffix: str) -> str:
        """返回兼容单 manager 旧 ID、同时支持连接池隔离的资源 ID。"""

        prefix = (
            "codex"
            if self._resource_namespace == "codex"
            else (f"codex-{self._resource_namespace}")
        )
        return f"{prefix}-{kind}:{suffix}"

    def _connection_id(self, generation: int) -> str:
        """返回一个 Codex 连接代际在资源账本中的 owner ID。"""

        return self._resource_id("connection", str(generation))

    def _register_connection_resource(
        self,
        client: AppServerClient,
        generation: int,
    ) -> None:
        """登记当前 app-server 进程组；测试替身没有 PID 时登记连接 handle。"""

        registry = self._resource_registry
        if registry is None:
            return
        connection_id = self._connection_id(generation)
        resource_id = self._resource_id("app-server", str(generation))
        if client.pid is None:
            registry.register_handle(
                resource_id=resource_id,
                owner_scope=OwnerScope.RUNTIME_CONNECTION,
                resource_kind="codex_app_server_connection",
                runtime="codex",
                runtime_generation=generation,
                runtime_connection_id=connection_id,
                connection_id=connection_id,
            )
        else:
            registry.register_process_group(
                resource_id=resource_id,
                owner_scope=OwnerScope.RUNTIME_CONNECTION,
                resource_kind="codex_app_server_process_group",
                pid=client.pid,
                runtime="codex",
                runtime_generation=generation,
                runtime_connection_id=connection_id,
            )
        self._connection_resource_ids[generation] = resource_id

    def _register_thread_resource(
        self,
        session: CodexSession,
        thread_id: str,
    ) -> None:
        """把已加载 thread 代表的 MCP、命令和 pending 生命周期登记到 session。"""

        registry = self._resource_registry
        if registry is None:
            return
        generation = self._active_generation
        key = (session.session_id, generation)
        if key in self._thread_resource_ids:
            return
        resource_id = self._resource_id("thread", f"{session.session_id}:{generation}")
        registry.register_handle(
            resource_id=resource_id,
            owner_scope=OwnerScope.SESSION,
            resource_kind="codex_thread_resources",
            runtime="codex",
            runtime_generation=generation,
            runtime_connection_id=self._connection_id(generation),
            agent_session_id=session.session_id,
            connection_id=thread_id,
        )
        self._thread_resource_ids[key] = resource_id

    def _register_turn_resource(self, session: CodexSession, turn_id: str) -> None:
        """登记已经被 app-server 接受但尚未终结的 Codex turn。"""

        registry = self._resource_registry
        if registry is None:
            return
        resource_id = self._resource_id("turn", f"{session.session_id}:{turn_id}")
        registry.register_handle(
            resource_id=resource_id,
            owner_scope=OwnerScope.TURN,
            resource_kind="codex_turn",
            runtime="codex",
            runtime_generation=self._active_generation,
            runtime_connection_id=self._connection_id(self._active_generation),
            agent_session_id=session.session_id,
            turn_id=turn_id,
        )
        self._turn_resource_ids[(session.session_id, turn_id)] = resource_id

    def _mark_turn_resource_closed(self, session_id: str, turn_id: str) -> None:
        """把原生 terminal 或 thread archive 确认的 turn owner 提交为 closed。"""

        registry = self._resource_registry
        resource_key = (session_id, turn_id)
        resource_id = self._turn_resource_ids.get(resource_key)
        if registry is None or resource_id is None:
            return
        registry.mark_owner_closing(
            OwnerScope.TURN,
            agent_session_id=session_id,
            turn_id=turn_id,
        )
        registry.mark_closed(resource_id)
        summary = registry.owner_summary(
            OwnerScope.TURN,
            agent_session_id=session_id,
            turn_id=turn_id,
        )
        if summary.live_resource_count:
            registry.mark_owner_needs_reconcile(
                OwnerScope.TURN,
                agent_session_id=session_id,
                turn_id=turn_id,
                remaining_resource_count=summary.live_resource_count,
            )
        else:
            registry.mark_owner_closed(
                OwnerScope.TURN,
                agent_session_id=session_id,
                turn_id=turn_id,
            )
        self._turn_resource_ids.pop(resource_key, None)

    def _mark_connection_needs_reconcile(
        self,
        generation: int,
        error: str,
    ) -> None:
        """保留未完成进程树核验的 app-server 记录供 Host 或下次启动处理。"""

        registry = self._resource_registry
        resource_id = self._connection_resource_ids.get(generation)
        if registry is not None and resource_id is not None:
            registry.mark_needs_reconcile(resource_id, error)
            registry.mark_owner_needs_reconcile(
                OwnerScope.RUNTIME_CONNECTION,
                runtime_connection_id=self._connection_id(generation),
            )

    def _default_client_factory(self) -> AppServerClient:
        """返回未启动的默认 client；handler 注册与启动由 ``ensure_ready`` 完成。"""

        return AppServerClient(
            process_controller=(
                self._resource_registry.process_controller
                if self._resource_registry is not None
                else None
            )
        )

    def _thread_start_params(self, session: CodexSession) -> dict[str, Any]:
        """保留会话配置中的非空覆盖项，并合并启用的 Trowel MCP 服务。"""

        return manager_params.thread_start_params(session)

    def _thread_resume_params(self, session: CodexSession) -> dict[str, Any]:
        """为已有 binding 重发工作目录、权限覆盖和不持久化的 MCP 配置。"""

        return manager_params.thread_resume_params(session)

    @staticmethod
    def _turn_start_params(
        thread_id: str,
        text: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        approval: str | None = None,
        sandbox: str | None = None,
    ) -> dict[str, Any]:
        """构造文本输入，并只发送本轮显式提供的模型、推理强度和权限覆盖。"""

        return manager_params.turn_start_params(
            thread_id,
            text,
            model=model,
            effort=effort,
            approval=approval,
            sandbox=sandbox,
        )


def _extract_thread_id(params: Mapping[str, Any]) -> str | None:
    """只读取通知顶层 ``threadId``。

    全局通知和 ``thread/started`` 都返回 ``None``；前者由上游分支广播，
    后者由 translator 的忽略列表处理。
    """

    value = params.get("threadId")
    if isinstance(value, str) and value:
        return value
    return None


def _extract_turn_id_from_params(params: Mapping[str, Any]) -> str | None:
    """读取通知顶层的非空轮次 ID。"""

    value = params.get("turnId")
    return value if isinstance(value, str) and value else None


def _extract_turn_id(turn_result: Mapping[str, Any]) -> str:
    """从启动响应提取已创建的 ``turn.id``；缺失即表示协议漂移。"""

    turn = turn_result.get("turn")
    if not isinstance(turn, Mapping) or not turn.get("id"):
        raise ProtocolViolationError(
            "turn/start response has no turn.id",
            payload=dict(turn_result),
        )
    return str(turn["id"])
