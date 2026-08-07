"""协调 CC 与 Codex 会话的绑定、路由和生命周期。

持久化 binding 是会话创建后唯一的 runtime 路由依据；模型和界面状态都不能代替它。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from trowel_py.agent_capacity import (
    DELEGATE_CONNECTION_LIMIT,
    DELEGATE_RUNNING_LIMIT,
    USER_CONNECTION_LIMIT,
    USER_RUNNING_LIMIT,
)
from trowel_py.agent_host.binding import (
    Runtime,
    SessionBinding,
    TitleSource,
    make_binding,
)
from trowel_py.agent_host.capacity import (
    CapacityConflictError,
    CapacityLimitError,
    CapacityLimits,
    SessionCapacityGate,
)
from trowel_py.agent_host.capabilities import (
    CC_CAPABILITIES,
    CODEX_CAPABILITIES,
)
from trowel_py.agent_host.codex_launch import (
    _CODEX_PERMISSION_PRESETS,
    _injection_fingerprint,
    prepare_codex_session,
)
from trowel_py.agent_host.codex_settings import (
    NoUsableEffortError,
    UnknownModelError,
    select_turn_settings,
)
from trowel_py.agent_host.delegate_identity import (
    NonUserIdentityStore,
    non_user_identity_path,
)
from trowel_py.agent_host.events import AgentEvent
from trowel_py.agent_host.lifecycle import (
    SessionCloseResult,
    SessionInFlightError,
    SessionLifecycle,
    SessionReconcileRequiredError,
)
from trowel_py.agent_host.live_events import (
    ApplicationEventBroadcaster,
    ApplicationEventSubscription,
)
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.agent_host.session_titles import (
    SessionTitleGenerator,
    clean_generated_title,
    prompt_title,
)
from trowel_py.agent_host.runtimes import (
    ClaudeCodeEventAdapter,
    ClaudeCodeRuntimeAdapter,
    CodexEventAdapter,
    CodexRuntimeAdapter,
    RuntimeSessionPort,
)
from trowel_py.agent_host.store import BindingStore, next_session_display_name
from trowel_py.agent_host.configuration_archive import (
    FrozenSessionConfiguration,
    SessionConfigurationArchive,
    resolve_configuration_archive_path,
)
from trowel_py.agent_host.title_store import (
    SessionTitleRecord,
    SessionTitleStore,
    resolve_title_store_path,
)
from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.session_lifecycle import (
    CcCapacityError,
    CcWorkdirNotFoundError,
)
from trowel_py.codex_host.commands import reserved_command_name
from trowel_py.codex_host.pending_requests import (
    PendingRequestConflictError,
    PendingRequestDecisionError,
    PendingRequestNotFoundError,
    PendingRequestOwnershipError,
)
from trowel_py.codex_host.session import TurnConflictError
from trowel_py.resource_lifecycle.registry import ResourceRegistry
from trowel_py.configuration.runtime_launch import (
    RuntimeLaunchConfiguration,
    cleanup_private_claude_settings,
    write_private_claude_settings,
)

_log = logging.getLogger(__name__)

# 连接上限按仍有 binding 的已注册 session/thread 计数，共享 manager 不合并名额。
MAX_CONNECTIONS = USER_CONNECTION_LIMIT
# 用户会话的在跑上限由前后端共同执行；该常量保留公开兼容。
MAX_RUNNING = USER_RUNNING_LIMIT
# 委派子会话由后端单独限制，两个 runtime 共用同一组连接和在跑名额。
MAX_DELEGATE_CONNECTIONS = DELEGATE_CONNECTION_LIMIT
MAX_DELEGATE_RUNNING = DELEGATE_RUNNING_LIMIT

_TURN_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error"})


@dataclass(frozen=True)
class FrozenConnectionExpectation:
    """保存内部 owner 在 runtime 创建前必须匹配的连接冻结快照。

    Attributes:
        connection_identity_version: discussion 创建时冻结的连接身份版本。
        capability_version: 创建时放行 runtime/model 组合的能力表版本。
        capability_source: 创建时能力结论的真实证据说明。
    """

    connection_identity_version: int | None
    capability_version: str | None
    capability_source: str | None


class AgentTurnObserver(Protocol):
    """声明 SessionHub 启动或异常结束 turn 时使用的窄观察端口。"""

    def prepare_turn(
        self,
        session_id: str,
        runtime: Literal["claude_code", "codex"],
    ) -> str:
        """在进入原生 runtime 前保存当前请求关联并返回观察代次。"""

    def abort_turn(self, session_id: str, observation_id: str) -> None:
        """只收口指定代次中没有正常 terminal 的观察状态。"""


class SessionHubError(Exception):
    """表示 Session Hub 拒绝请求或未能完成会话操作。"""


class InvalidSessionRequestError(SessionHubError):
    """表示创建或操作会话时提供的参数无效。"""


class SessionNotFoundError(SessionHubError):
    """表示 Trowel 会话记录或对应的 Claude Code、Codex 会话不存在。"""


class SessionAccessError(SessionHubError):
    """表示请求试图操作属于其他会话的资源。"""


class SessionConflictError(SessionHubError):
    """表示请求与会话当前状态、连接上限或并发操作冲突。"""


class SessionOperationError(SessionHubError):
    """表示当前运行工具不支持该操作，或提供的参数组合不适用。"""


def _reject_reserved_codex_command(text: str) -> None:
    """阻止把 Codex 专用斜杠命令作为普通消息发送。

    Args:
        text: 准备用于启动 Codex 轮次的文本。

    Raises:
        SessionOperationError: 文本是由 Trowel 单独处理的 Codex 斜杠命令。
    """

    reserved = reserved_command_name(text)
    if reserved is not None:
        raise SessionOperationError(
            f"/{reserved} is a local command and cannot start a Codex turn"
        )


class RuntimeUnavailableError(SessionHubError):
    """表示目标运行工具的管理组件当前不可用。"""


class RuntimeTurnError(SessionHubError):
    """表示 Claude Code 或 Codex 未能完成请求，或操作结果未能保存。"""


class RuntimeFrozenError(SessionOperationError):
    """表示试图把已创建的会话从 Claude Code 改为 Codex，或反向修改。"""


class CrossRuntimeResumeError(SessionConflictError):
    """表示恢复历史会话时选择的运行工具与原会话记录不一致。"""


class ConditionMismatchError(SessionConflictError):
    """表示恢复历史会话时，请求的上下文注入或工具开关与创建时不一致。"""


# 生产 opener 与测试替身共享调用协议但具体类型不同，因此保持宽松 Callable。
CcOpener = Callable[..., Any]
SessionReviewRequester = Callable[[SessionBinding], None]
ConfigurationResolver = Callable[[str, str, str | None], RuntimeLaunchConfiguration]
LastChoiceRecorder = Callable[[RuntimeLaunchConfiguration], None]
AgentDefaultsResolver = Callable[[Mapping[str, Any] | None], dict[str, Any] | None]


def _default_cc_registry() -> dict[str, Any]:
    """返回 Claude Code 路由当前登记的 Trowel 会话 ID 与会话对象对应表。"""

    from trowel_py.cc_host import routes as cc_routes

    return cc_routes.get_registry()


def _default_cc_opener() -> CcOpener:
    """返回 Claude Code 路由提供的默认会话创建函数。"""

    from trowel_py.cc_host import routes as cc_routes

    return cc_routes.open_cc_session_configured


class SessionHub:
    """以 binding 为事实源协调两种 runtime，不持有原生会话实现。"""

    def __init__(
        self,
        store: BindingStore,
        codex_manager: Any | None = None,
        *,
        cc_registry: dict[str, Any] | None = None,
        cc_opener: CcOpener | None = None,
        cc_proxy_base_url: str | None = None,
        cc_settings_path: str | Path | None = None,
        codex_config_home: str | Path | None = None,
        event_observer: Callable[[Mapping[str, Any]], None] | None = None,
        turn_observer: AgentTurnObserver | None = None,
        non_user_identity_store: NonUserIdentityStore | None = None,
        runtime_ports: Mapping[Runtime, RuntimeSessionPort] | None = None,
        capacity_limits: CapacityLimits | None = None,
        session_review_requester: SessionReviewRequester | None = None,
        title_generator: SessionTitleGenerator | None = None,
        title_store: SessionTitleStore | None = None,
        codex_history_root: str | Path | None = None,
        resource_registry: ResourceRegistry | None = None,
        runtime_availability: Mapping[Runtime, bool] | None = None,
        configuration_resolver: ConfigurationResolver | None = None,
        last_choice_recorder: LastChoiceRecorder | None = None,
        agent_defaults_resolver: AgentDefaultsResolver | None = None,
        cc_connection_proxy_registry: Any | None = None,
        configuration_archive: SessionConfigurationArchive | None = None,
        require_configured_connections: bool | Callable[[], bool] = False,
        private_claude_settings_directory: str | Path | None = None,
    ) -> None:
        """创建统一管理 Claude Code 与 Codex 会话的 Session Hub。

        Args:
            store: 保存 Trowel 会话记录的存储对象。
            codex_manager: Codex 进程和 thread 管理器；未提供时不启用 Codex。
            cc_registry: Claude Code 会话 ID 与会话对象的对应表；未提供时使用路由
                模块当前登记的会话。
            cc_opener: 创建 Claude Code 会话的函数；未提供时使用路由模块的默认
                函数。
            cc_proxy_base_url: Claude Code 请求模型时使用的代理地址。
            cc_settings_path: Claude Code 配置文件路径；使用代理时从中读取模型
                服务商所需的环境变量。
            codex_config_home: Codex 配置目录；创建会话前检查其中是否存在同名 MCP。
            event_observer: 接收每个通用事件的同步回调。
            turn_observer: 在 runtime 请求边界保存调用关联并收口异常 turn 的观察端口。
            non_user_identity_store: 跨 binding 清理保留所有非用户原生会话 ID 的
                本机索引；未提供时在 binding 文件旁创建独立索引。
            runtime_ports: 两种 runtime 的统一状态与关闭入口；未提供时根据 registry
                和 Codex manager 构造默认适配器。
            capacity_limits: 用户连接和委派资源池上限；未提供时使用生产默认值。
            session_review_requester: 用户会话关闭后持久登记 Memory review 的同步
                回调；未提供时只执行原有关闭流程。
            title_generator: 用低成本临时模型生成语义标题的异步实现；未提供时只
                保存首条提示词预览。
            title_store: 按原生会话 ID 保存标题的独立索引；未提供时在 binding
                文件旁创建。
            codex_history_root: Codex normalized turn journals 所在的 Memory 根目录；
                未提供时只使用原生 ``thread/read`` 回放。
            resource_registry: 当前应用实例的临时资源账本；未提供时保持进程内兼容
                行为，不执行跨 runtime 资源归零复核。
            runtime_availability: 两种 runtime CLI 在当前系统中的安装状态；未提供时
                保持测试与旧调用方的既有可用性判断。
            configuration_resolver: 把连接、模型和 effort 解析成秘密启动配置的端口。
            last_choice_recorder: 原生会话建立后写回连接最近选择的端口。
            agent_defaults_resolver: 把设置页默认条件合并到最近会话选择的同步端口。
            cc_connection_proxy_registry: Claude 会话租约到冻结上游的进程内 registry。
            configuration_archive: binding 删除后继续保存原生会话冻结条件的档案。
            require_configured_connections: 是否拒绝没有设置域连接的用户会话；也可
                传入实时判断函数，让应用启动后新增的首个连接立即接管普通 Agent。
                旧测试和内部临时调用默认保持兼容。
            private_claude_settings_directory: Claude 连接级私有 settings 的应用自有
                目录；未提供时使用 binding 文件旁的运行时私有目录。
        """

        self._store = store
        self._title_store = title_store or SessionTitleStore(
            resolve_title_store_path(store.path)
        )
        self._title_generator = title_generator
        self._title_lock = threading.Lock()
        self._non_user_identities = non_user_identity_store or NonUserIdentityStore(
            non_user_identity_path(store.path)
        )
        self._codex = codex_manager
        self._runtime_availability = (
            dict(runtime_availability)
            if runtime_availability is not None
            else {
                Runtime.CLAUDE_CODE: True,
                Runtime.CODEX: codex_manager is not None,
            }
        )
        self._cc_registry = (
            cc_registry if cc_registry is not None else _default_cc_registry()
        )
        self._cc_opener = cc_opener if cc_opener is not None else _default_cc_opener()
        self._cc_proxy_base_url = cc_proxy_base_url
        self._cc_settings_path = cc_settings_path
        self._configuration_resolver = configuration_resolver
        self._last_choice_recorder = last_choice_recorder
        self._agent_defaults_resolver = agent_defaults_resolver
        self._cc_connection_proxy_registry = cc_connection_proxy_registry
        self._pending_last_choices: dict[str, RuntimeLaunchConfiguration] = {}
        self._configuration_archive = configuration_archive or (
            SessionConfigurationArchive(resolve_configuration_archive_path(store.path))
        )
        self._require_configured_connections = require_configured_connections
        self._private_claude_settings_directory = (
            Path(private_claude_settings_directory)
            if private_claude_settings_directory is not None
            else store.path.parent / "runtime-private" / "claude-settings"
        )
        cleanup_private_claude_settings(self._private_claude_settings_directory)
        self._codex_config_home = (
            Path(codex_config_home) if codex_config_home is not None else None
        )
        self._event_observer = event_observer
        self._turn_observer = turn_observer
        self._resource_registry = resource_registry
        self._session_review_requester = session_review_requester
        self._codex_history_root = (
            Path(codex_history_root) if codex_history_root is not None else None
        )
        self._active_id: str | None = None
        self._draining = False
        # adapter 跨 turn 复用；被 adapter 丢弃的原生事件不占统一序号。
        self._cc_adapters: dict[str, ClaudeCodeEventAdapter] = {}
        self._codex_adapters: dict[str, CodexEventAdapter] = {}
        self._codex_event_subscribers: dict[
            str, set[asyncio.Queue[dict[str, Any] | None]]
        ] = {}
        self._agent_event_subscribers: dict[
            str, set[asyncio.Queue[dict[str, Any] | None]]
        ] = {}
        self._application_events = ApplicationEventBroadcaster()
        self._live_generation = uuid.uuid4().hex
        self._session_state_generations: dict[str, int] = {}
        self._current_root_turn_ids: dict[str, str | None] = {}
        self._last_root_turn_states: dict[str, str] = {}
        self._last_event_sequences: dict[str, int] = {}
        self._codex_event_tasks: dict[str, asyncio.Task[None]] = {}
        self._detached_turn_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_close_tasks: dict[str, asyncio.Task[SessionCloseResult]] = {}
        self._closed_session_results: dict[str, SessionCloseResult] = {}
        self._closing_session_ids: set[str] = set()
        self._turn_idle_conditions: dict[str, asyncio.Condition] = {}
        self._session_create_requests: dict[
            str, tuple[str, asyncio.Task[SessionBinding]]
        ] = {}
        self._runtime_ports: dict[Runtime, RuntimeSessionPort] = (
            dict(runtime_ports)
            if runtime_ports is not None
            else {
                Runtime.CLAUDE_CODE: ClaudeCodeRuntimeAdapter(self._cc_registry),
                Runtime.CODEX: CodexRuntimeAdapter(self._codex),
            }
        )
        self._capacity = SessionCapacityGate(
            self._store,
            self._runtime_ports,
            capacity_limits
            or CapacityLimits(
                user_connections=MAX_CONNECTIONS,
                delegate_connections=MAX_DELEGATE_CONNECTIONS,
                delegate_running=MAX_DELEGATE_RUNNING,
            ),
        )
        self._lifecycle = SessionLifecycle(
            self._store,
            self._non_user_identities,
            self._runtime_ports,
            self._capacity,
            self._resource_registry,
        )
        self._lifecycle.migrate_non_user_identities()

    async def coalesce_session_create(
        self,
        request_id: str,
        fingerprint: str,
        operation: Callable[[], Awaitable[SessionBinding]],
    ) -> SessionBinding:
        """按 renderer 请求 ID 合并可能因客户端超时而重试的会话创建。

        Args:
            request_id: renderer 为一次逻辑创建生成的稳定 ID。
            fingerprint: 创建参数的稳定表示，防止同一 ID 被挪作他用。
            operation: 真正执行一次创建并返回 binding 的异步函数。

        Returns:
            首次创建或同 ID 已完成创建得到的同一个 binding。

        Raises:
            InvalidSessionRequestError: 同一请求 ID 携带了不同创建参数。
        """

        existing = self._session_create_requests.get(request_id)
        if existing is not None:
            existing_fingerprint, task = existing
            if existing_fingerprint != fingerprint:
                raise InvalidSessionRequestError(
                    "session create request ID was reused with different parameters"
                )
        else:
            task = asyncio.create_task(
                operation(), name=f"agent-session-create:{request_id}"
            )
            self._session_create_requests[request_id] = (fingerprint, task)

            def discard_failed(completed: asyncio.Task[SessionBinding]) -> None:
                """失败请求允许使用同一 ID 重试；成功结果保留供未知接收状态对账。"""

                if completed.cancelled() or completed.exception() is not None:
                    if self._session_create_requests.get(request_id) == (
                        fingerprint,
                        completed,
                    ):
                        self._session_create_requests.pop(request_id, None)

            task.add_done_callback(discard_failed)
        return await asyncio.shield(task)

    @property
    def store(self) -> BindingStore:
        """获取保存了 Trowel 会话记录的存储对象。"""

        return self._store

    @property
    def codex_available(self) -> bool:
        """是否已经配置 Codex 进程和 thread 管理器。"""

        return self._codex is not None

    def runtime_available(self, runtime: Runtime) -> bool:
        """返回指定 runtime 的 CLI 是否已安装且对应 Host 已配置。"""
        if runtime == Runtime.CODEX and self._codex is None:
            return False
        return self._runtime_availability.get(runtime, False)

    def non_user_native_ids(self, runtime: Runtime) -> frozenset[str]:
        """返回必须从公开历史与恢复入口排除的原生会话身份。

        Args:
            runtime: 要查询 Claude Code 会话 ID 或 Codex thread ID 的运行工具。

        Returns:
            委派、探针和 discussion 等非用户会话的长期身份集合。
        """

        return self._non_user_identities.ids(runtime)

    def is_non_user_native_id(self, runtime: Runtime, native_session_id: str) -> bool:
        """判断原生会话是否由内部 owner 持有，不能公开恢复。

        Args:
            runtime: 原生身份所属运行工具。
            native_session_id: Claude Code 会话 ID 或 Codex thread ID。

        Returns:
            该身份已经登记为非用户会话时为 True。
        """

        return native_session_id in self.non_user_native_ids(runtime)

    @property
    def draining(self) -> bool:
        """返回 Agent Host 是否已经拒绝新的会话和轮次。"""

        return self._draining

    def begin_drain(self) -> None:
        """幂等进入应用退出状态，使后续 create 和 send 立即失败。"""

        self._draining = True

    def _require_accepting_work(self) -> None:
        """应用退出期间拒绝创建会话或启动新轮次。"""

        if self._draining:
            raise SessionConflictError("应用正在退出，不能创建会话或启动新轮次")

    def _initial_title(self, req: CreateAgentSessionRequest) -> tuple[str, TitleSource]:
        """恢复原生会话时取得 Trowel 标题或运行工具提供的标题。

        独立标题索引优先于历史接口刚读到的原生标题，使 binding 被关闭后，手动
        标题和自动标题仍能在下次恢复时出现。
        """

        if req.session_kind != "user":
            return "", "new"
        native_session_id = req.resume_from
        if native_session_id is None:
            return "", "new"
        saved = self._title_store.get(Runtime(req.runtime), native_session_id)
        if saved is not None:
            return saved.title, saved.source
        if req.resume_title is not None:
            return prompt_title(req.resume_title), "native"
        return "", "new"

    def _persist_native_title(self, binding: SessionBinding) -> None:
        """在 binding 已取得原生会话 ID 后保存当前非空标题。"""

        if (
            binding.native_session_id is None
            or not binding.display_title
            or binding.title_source == "new"
        ):
            return
        self._title_store.put(
            SessionTitleRecord(
                runtime=binding.runtime,
                native_session_id=binding.native_session_id,
                title=binding.display_title,
                source=binding.title_source,
                updated_at=binding.updated_at,
            )
        )

    def _replace_title(
        self,
        binding: SessionBinding,
        title: str,
        source: TitleSource,
    ) -> SessionBinding:
        """更新 binding 标题并同步独立的原生会话标题索引。"""

        updated = replace(
            binding,
            display_title=title,
            title_source=source,
            updated_at=datetime.now().isoformat(timespec="microseconds"),
        )
        self._store.put(updated)
        self._persist_native_title(updated)
        return updated

    def rename_title(self, session_id: str, title: str) -> SessionBinding:
        """把用户会话改为手动标题，后续后台生成不能覆盖它。

        Args:
            session_id: 要改名的 Trowel 会话 ID。
            title: API 已去除首尾空白的非空标题。

        Returns:
            保存手动标题后的会话记录。

        Raises:
            SessionNotFoundError: 找不到指定会话。
            SessionOperationError: 指定会话是内部委派会话。
        """

        with self._title_lock:
            binding = self._require(session_id)
            if binding.session_kind != "user":
                raise SessionOperationError(
                    "delegate session cannot have a display title"
                )
            return self._replace_title(binding, title, "manual")

    async def generate_title(self, session_id: str, text: str) -> SessionBinding:
        """先保存提示词预览，再尝试用临时低成本模型替换为语义标题。

        模型失败或超时时保留提示词预览。等待模型期间若用户已经手动改名，模型结果
        会被丢弃，避免迟到的后台任务覆盖用户选择。

        Args:
            session_id: 收到首条用户消息的 Trowel 会话 ID。
            text: 要概括的首条真实用户消息。

        Returns:
            当前最终生效的会话记录。

        Raises:
            SessionNotFoundError: 找不到指定会话。
            SessionOperationError: 指定会话是内部委派会话。
        """

        expected_prompt = prompt_title(text)
        with self._title_lock:
            binding = self._require(session_id)
            if binding.session_kind != "user":
                raise SessionOperationError(
                    "delegate session cannot have a display title"
                )
            if binding.title_source not in {"new", "prompt"}:
                return binding
            if not expected_prompt:
                return binding
            if binding.title_source == "prompt":
                if binding.display_title != expected_prompt:
                    return binding
                fallback = binding.display_title
            else:
                fallback = expected_prompt
                binding = self._replace_title(binding, fallback, "prompt")
        generator = self._title_generator
        if generator is None:
            return binding
        try:
            generated = await generator.generate(binding.runtime, text, binding.workdir)
        except Exception:  # noqa: BLE001 - 标题是可降级的旁路任务。
            _log.warning(
                "session title generation failed for %s",
                session_id,
                exc_info=True,
            )
            return self._require(session_id)
        cleaned = clean_generated_title(generated)
        if cleaned is None:
            return self._require(session_id)
        with self._title_lock:
            latest = self._require(session_id)
            if latest.title_source != "prompt" or latest.display_title != fallback:
                return latest
            return self._replace_title(latest, cleaned, "generated")

    def create(
        self,
        req: CreateAgentSessionRequest,
        *,
        frozen_connection: FrozenConnectionExpectation | None = None,
        bootstrap_context: str | None = None,
    ) -> SessionBinding:
        """创建 Claude Code 或 Codex 会话，并保存对应的 Trowel 会话记录。

        此方法只完成会话登记和配置保存，不发送消息或启动轮次。

        Args:
            req: 运行工具、工作目录、模型、权限和上下文开关等创建配置。
            frozen_connection: 内部 owner 可传入的创建前连接身份与能力快照。
            bootstrap_context: 内部 owner 提供的系统级首轮背景。

        Returns:
            新创建的 Trowel 会话记录。

        Raises:
            InvalidSessionRequestError: 工作目录不存在或创建参数无效。
            SessionConflictError: 连接数已满，或 Codex 配置中存在同名 MCP。
            RuntimeUnavailableError: 请求使用 Codex，但未配置 Codex 会话管理器。
        """

        self._require_accepting_work()
        req = self._inherit_resume_config(req)
        if not Path(req.workdir).is_dir():
            raise InvalidSessionRequestError("workdir does not exist")
        launch = self._resolve_launch(req, frozen_connection=frozen_connection)
        memory_mcp_enabled = self._resolve_memory_mcp(req, launch)
        try:
            with self._capacity.admit_connection(req.session_kind):
                if req.runtime == "claude_code":
                    binding = self._create_cc(
                        req, launch, memory_mcp_enabled, bootstrap_context
                    )
                else:
                    binding = self._create_codex(
                        req, launch, memory_mcp_enabled, bootstrap_context
                    )
                self._touch_session_state(binding.session_id)
                return binding
        except CapacityLimitError as exc:
            raise SessionConflictError(str(exc)) from exc

    def _inherit_resume_config(
        self, req: CreateAgentSessionRequest
    ) -> CreateAgentSessionRequest:
        """恢复历史会话时，用最近一次 Trowel 会话记录补全未明确指定的配置。

        Claude Code 会沿用模型、思考强度和权限模式，Codex 会沿用权限预设；两者
        都会沿用 Memory、Profile 和 Self 开关。

        Args:
            req: 包含 Claude Code 会话 ID 或 Codex thread ID 的创建请求。

        Returns:
            补全后的创建请求；没有对应历史记录时原样返回。
        """

        if req.resume_from is None:
            return req
        previous = self._frozen_resume_configuration(
            Runtime(req.runtime), req.resume_from
        )
        if previous is None:
            return req
        explicit = req.model_fields_set
        updates: dict[str, Any] = {}
        if "connection_id" in explicit:
            if (
                previous.connection_id is not None
                and req.connection_id != previous.connection_id
            ):
                raise ConditionMismatchError(
                    "resumed session is frozen to its original connection"
                )
        else:
            updates["connection_id"] = previous.connection_id
        # 旧 Codex binding 依赖 native thread 自己恢复有效模型；只有设置域连接
        # 创建的 Codex thread 才把用户选择的模型与 effort 当作冻结条件。
        freeze_model_selection = (
            req.runtime == "claude_code" or previous.connection_id is not None
        )
        if freeze_model_selection:
            for field in ("model", "effort"):
                frozen_field = f"requested_{field}"
                frozen_value = getattr(previous, frozen_field, getattr(previous, field))
                if field in explicit:
                    if frozen_value is not None and getattr(req, field) != frozen_value:
                        raise ConditionMismatchError(
                            f"resumed session is frozen to its original {field}"
                        )
                else:
                    updates[field] = frozen_value
        permission_field = (
            "permission_mode" if req.runtime == "claude_code" else "permission_preset"
        )
        frozen_permission = (
            previous.permission
            if req.runtime == "claude_code"
            else previous.permission_preset
        )
        if permission_field in explicit:
            if (
                frozen_permission is not None
                and getattr(req, permission_field) != frozen_permission
            ):
                raise ConditionMismatchError(
                    "resumed session is frozen to its original permission"
                )
        else:
            updates[permission_field] = frozen_permission
        for request_field, binding_field in (
            ("memory_enabled", "memory_enabled"),
            ("profile_enabled", "profile_enabled"),
            ("self_enabled", "self_enabled"),
            ("agent_mcp_enabled", "agent_mcp_enabled"),
        ):
            if request_field not in explicit:
                updates[request_field] = getattr(previous, binding_field)
        return req.model_copy(update=updates)

    def _resolve_launch(
        self,
        req: CreateAgentSessionRequest,
        *,
        frozen_connection: FrozenConnectionExpectation | None = None,
    ) -> RuntimeLaunchConfiguration | None:
        """解析显式连接，并拒绝 runtime 不一致或无法验证的启动配置。"""

        if req.connection_id is None:
            configured_connections_required = self._require_configured_connections
            if callable(configured_connections_required):
                configured_connections_required = configured_connections_required()
            if configured_connections_required and req.session_kind == "user":
                if req.resume_from is not None:
                    raise InvalidSessionRequestError(
                        "该历史会话没有可用的冻结连接，暂时无法直接恢复；"
                        "当前可先新建会话"
                    )
                raise InvalidSessionRequestError("必须选择一项已验证的 Runtime 连接")
            return None
        if self._configuration_resolver is None:
            raise RuntimeUnavailableError("connection configuration is unavailable")
        if not req.model:
            raise InvalidSessionRequestError("connection-backed session requires model")
        try:
            launch = self._configuration_resolver(
                req.connection_id, req.model, req.effort
            )
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001 - 设置域错误统一映射到创建边界。
            raise InvalidSessionRequestError(str(exc)) from exc
        if launch.runtime.value != req.runtime:
            raise InvalidSessionRequestError(
                "selected connection does not belong to the requested runtime"
            )
        if req.agent_mcp_enabled:
            raise InvalidSessionRequestError(
                "agent MCP is not verified for connection-backed sessions"
            )
        if req.resume_from is not None:
            frozen = self._frozen_resume_configuration(
                Runtime(req.runtime), req.resume_from
            )
            if (
                frozen is not None
                and frozen.connection_identity_version is not None
                and launch.connection_identity_version
                != frozen.connection_identity_version
            ):
                raise ConditionMismatchError(
                    "saved connection identity changed; manual rebind is required"
                )
        if frozen_connection is not None:
            if req.session_kind != "discussion":
                raise InvalidSessionRequestError(
                    "frozen connection expectation is reserved for discussion"
                )
            expected = (
                frozen_connection.connection_identity_version,
                frozen_connection.capability_version,
                frozen_connection.capability_source,
            )
            actual = (
                launch.connection_identity_version,
                launch.capability_version,
                launch.capability_source,
            )
            if actual != expected:
                raise ConditionMismatchError(
                    "discussion connection identity or capability changed; "
                    "manual rebind is required"
                )
        return launch

    def _resolve_memory_mcp(
        self,
        req: CreateAgentSessionRequest,
        launch: RuntimeLaunchConfiguration | None,
    ) -> bool:
        """计算并冻结 Memory MCP 开关，不改变 Memory 正文注入语义。

        传统本机配置继续沿用 ``memory_enabled``。设置域连接尚未逐一验证 MCP
        透传，因此新会话一律关闭；恢复时沿用原会话已经冻结的内部值。
        """

        if launch is None:
            return req.memory_enabled
        if req.resume_from is None:
            return False
        previous = self._frozen_resume_configuration(
            Runtime(req.runtime), req.resume_from
        )
        return previous.memory_mcp_enabled if previous is not None else False

    async def prepare_create_request(
        self, req: CreateAgentSessionRequest
    ) -> CreateAgentSessionRequest:
        """为会话创建请求补全可以沿用的历史配置。

        先读取最近一次 Trowel 会话记录。请求继续 Claude Code 历史会话时，如果记录中
        没有模型、思考强度或权限配置，再从 Claude Code 自己保存的历史文件中查找。

        Args:
            req: 尚未补全历史配置的会话创建请求。

        Returns:
            补全后的创建请求；没有可用历史配置时原样返回。
        """

        prepared = self._inherit_resume_config(req)
        if req.runtime != "claude_code" or req.resume_from is None:
            return prepared
        from trowel_py.cc_host.session_scan import read_session_config

        native = await asyncio.to_thread(
            read_session_config, req.workdir, req.resume_from
        )
        if native is None:
            return prepared
        explicit = req.model_fields_set
        updates: dict[str, Any] = {}
        for field in ("model", "effort"):
            if field not in explicit and getattr(prepared, field) is None:
                updates[field] = getattr(native, field)
        if "permission_mode" not in explicit and prepared.permission_mode is None:
            updates["permission_mode"] = native.permission_mode
        return prepared.model_copy(update=updates)

    async def create_complete_session(
        self,
        req: CreateAgentSessionRequest,
        *,
        frozen_connection: FrozenConnectionExpectation | None = None,
        bootstrap_context: str | None = None,
    ) -> SessionBinding:
        """执行公开路由和内部 owner 共用的完整创建/恢复事务。

        Args:
            req: 原始会话创建请求。
            frozen_connection: discussion participant 创建前必须匹配的连接冻结快照。
            bootstrap_context: 内部 owner 提供的系统级首轮背景。

        Returns:
            已登记并在需要时完成 Codex thread hydrate 的 binding。

        Raises:
            SessionHubError: 配置、恢复归属、runtime 创建或 hydrate 失败。
        """

        prepared = await self.prepare_create_request(req)
        explicit = prepared.model_fields_set
        if prepared.resume_from is not None:
            self.validate_resume(
                Runtime(prepared.runtime),
                prepared.resume_from,
                memory_enabled=(
                    prepared.memory_enabled if "memory_enabled" in explicit else None
                ),
                profile_enabled=(
                    prepared.profile_enabled if "profile_enabled" in explicit else None
                ),
                self_enabled=(
                    prepared.self_enabled if "self_enabled" in explicit else None
                ),
            )
        binding = self.create(
            prepared,
            frozen_connection=frozen_connection,
            bootstrap_context=bootstrap_context,
        )
        if prepared.resume_from is not None and prepared.runtime == "codex":
            try:
                return await self.hydrate_resume(binding.session_id)
            except BaseException:
                await self.delete(binding.session_id)
                raise
        return binding

    def _latest_binding(
        self,
        *,
        runtime: Runtime | None = None,
        native_session_id: str | None = None,
    ) -> SessionBinding | None:
        """查找符合条件且最近更新的 Trowel 会话记录。

        Args:
            runtime: 只查找由 Claude Code 或 Codex 运行的会话。
            native_session_id: 只查找指定 Claude Code 会话 ID 或 Codex thread ID
                对应的记录。

        Returns:
            最近更新的匹配记录；没有匹配记录时返回 None。
        """

        candidates = [
            binding
            for binding in self._store.list_all()
            if (runtime is None or binding.runtime is runtime)
            and (
                native_session_id is None
                or binding.native_session_id == native_session_id
            )
        ]
        if not candidates:
            return None
        return max(
            enumerate(candidates),
            key=lambda pair: (
                pair[1].updated_at,
                pair[1].created_at,
                pair[0],
            ),
        )[1]

    def _frozen_resume_configuration(
        self,
        runtime: Runtime,
        native_session_id: str,
    ) -> SessionBinding | FrozenSessionConfiguration | None:
        """按统一优先级读取恢复条件，避免 binding 与档案校验分叉。

        仍存在的 binding 是最新事实源；binding 已清理时才读取脱敏档案。
        """

        binding = self._latest_binding(
            runtime=runtime,
            native_session_id=native_session_id,
        )
        if binding is not None:
            return binding
        return self._configuration_archive.get(runtime, native_session_id)

    def latest_session_defaults(self) -> dict[str, Any] | None:
        """读取最近创建或使用的会话配置，作为新建会话的默认值。

        Returns:
            包含运行工具、模型、思考强度、权限、Memory 和 Profile 开关的配置。
            Claude Code 使用 permission_mode；Codex 使用 permission_preset。
            没有历史会话记录时返回 None。
        """

        bindings = [
            binding
            for binding in self._store.list_all()
            if binding.session_kind == "user"
        ]
        if not bindings:
            return None
        binding = max(
            enumerate(bindings),
            key=lambda pair: (pair[1].updated_at, pair[1].created_at, pair[0]),
        )[1]
        defaults: dict[str, Any] = {
            "runtime": binding.runtime.value,
            "model": binding.model or "",
            "effort": binding.effort or "",
            "permission_mode": (
                binding.permission or ""
                if binding.runtime is Runtime.CLAUDE_CODE
                else ""
            ),
            "memory_enabled": binding.memory_enabled,
            "profile_enabled": binding.profile_enabled,
        }
        if binding.connection_id is not None:
            defaults["connection_id"] = binding.connection_id
        if binding.runtime is Runtime.CODEX and binding.permission_preset is not None:
            defaults["permission_preset"] = binding.permission_preset
        return defaults

    def new_session_defaults(self) -> dict[str, Any] | None:
        """返回设置页默认条件与最近有效会话选择合并后的新建值。"""

        fallback = self.latest_session_defaults()
        if self._agent_defaults_resolver is None:
            return fallback
        return self._agent_defaults_resolver(fallback)

    async def hydrate_resume(self, session_id: str) -> SessionBinding:
        """将要继续的 Codex thread 加载到当前 Codex 进程，并更新 Trowel 会话记录。

        更新内容包括 Codex 实际采用的 thread ID、模型、思考强度和权限。Claude Code
        会话或没有历史 thread ID 的 Codex 会话不需要加载，直接返回原记录。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            更新后的 Trowel 会话记录。

        Raises:
            SessionNotFoundError: 找不到对应的 Trowel 会话或 Codex 会话。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionConflictError: 该 Codex thread 已被其他会话占用。
            RuntimeTurnError: Codex thread 加载或会话记录更新失败。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX or binding.native_session_id is None:
            return binding
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        try:
            await self._codex.attach(
                session,
                before_commit=lambda attached: self._writeback_codex_native(
                    session_id, attached
                ),
            )
        except TurnConflictError as exc:
            raise SessionConflictError(str(exc)) from exc
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为可诊断的 runtime 错误。
            raise RuntimeTurnError(f"codex resume failed: {exc}") from exc
        return self._require(session_id)

    def _create_cc(
        self,
        req: CreateAgentSessionRequest,
        launch: RuntimeLaunchConfiguration | None,
        memory_mcp_enabled: bool,
        bootstrap_context: str | None,
    ) -> SessionBinding:
        """登记 Claude Code 会话并保存初始的 Trowel 会话记录。

        未指定权限模式时使用 bypassPermissions。此时只创建会话对象，不启动
        Claude Code 进程或轮次。

        Args:
            req: 已选择 Claude Code 的 Agent 会话创建请求。

        Returns:
            新创建的 Trowel 会话记录。

        Raises:
            InvalidSessionRequestError: 工作目录不存在。
            SessionConflictError: Claude Code 会话数已达到上限。
        """

        if not self.runtime_available(Runtime.CLAUDE_CODE):
            raise RuntimeUnavailableError("Claude Code CLI 未安装")

        from trowel_py.cc_host.schemas import CreateSessionRequest

        cc_req = CreateSessionRequest(
            workdir=req.workdir,
            resume_from=req.resume_from,
            permission_mode=req.permission_mode or "bypassPermissions",
            model=req.model,
            effort=req.effort,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            self_enabled=req.self_enabled,
            session_kind=req.session_kind,
            agent_mcp_enabled=req.agent_mcp_enabled,
            delegation_depth=req.delegation_depth,
        )
        display_name = self._display_name(req.workdir)
        display_title, title_source = self._initial_title(req)
        resource_launch_config: dict[str, object] = {}
        if self._resource_registry is not None:
            resource_launch_config = {
                "process_controller": self._resource_registry.process_controller,
                "resource_registry": self._resource_registry,
            }
        proxy_base_url = self._cc_proxy_base_url
        settings_path = self._cc_settings_path
        owned_settings_path = False
        close_callback: Callable[[], None] | None = None
        connection_host_config: dict[str, object] = {}
        if launch is not None:
            registry = self._cc_connection_proxy_registry
            if registry is None or not self._cc_proxy_base_url or not launch.base_url:
                raise RuntimeUnavailableError("Claude connection proxy is unavailable")
            lease_token = registry.acquire(launch.base_url, launch.proxy_url)
            proxy_base_url = (
                f"{self._cc_proxy_base_url.rstrip('/')}/api/cc-runtime/{lease_token}"
            )
            try:
                settings_path = write_private_claude_settings(
                    launch.claude_settings(proxy_base_url=proxy_base_url),
                    directory=self._private_claude_settings_directory,
                )
            except BaseException:
                registry.release(lease_token)
                raise
            owned_settings_path = True

            def release_connection_lease() -> None:
                """在 Claude host 收口时释放当前会话的不透明上游租约。"""

                registry.release(lease_token)

            close_callback = release_connection_lease
            connection_host_config = {
                "owned_settings_path": True,
                "close_callback": close_callback,
                "memory_mcp_enabled": memory_mcp_enabled,
            }
        if bootstrap_context:
            connection_host_config["bootstrap_context"] = bootstrap_context
        if not req.memory_eligibility:
            connection_host_config["memory_eligibility"] = False
        try:
            opened = self._cc_opener(
                cc_req,
                self._cc_registry,
                proxy_base_url=proxy_base_url,
                settings_path=settings_path,
                display_name=display_name,
                **resource_launch_config,
                **connection_host_config,
            )
        except CcWorkdirNotFoundError as exc:
            self._rollback_claude_connection_settings(
                settings_path, owned_settings_path, close_callback
            )
            raise InvalidSessionRequestError(str(exc)) from exc
        except CcCapacityError as exc:
            self._rollback_claude_connection_settings(
                settings_path, owned_settings_path, close_callback
            )
            raise SessionConflictError(str(exc)) from exc
        except BaseException:
            self._rollback_claude_connection_settings(
                settings_path, owned_settings_path, close_callback
            )
            raise
        try:
            binding = make_binding(
                session_id=opened.sid,
                runtime=Runtime.CLAUDE_CODE,
                native_session_id=req.resume_from,
                workdir=req.workdir,
                model=req.model,
                effort=req.effort,
                permission=cc_req.permission_mode,
                memory_enabled=req.memory_enabled,
                memory_mcp_enabled=memory_mcp_enabled,
                profile_enabled=req.profile_enabled,
                self_enabled=req.self_enabled,
                session_kind=req.session_kind,
                memory_eligibility=req.memory_eligibility,
                agent_mcp_enabled=req.agent_mcp_enabled,
                parent_session_id=req.parent_session_id,
                delegation_depth=req.delegation_depth,
                owner_ref=req.owner_ref,
                capabilities=CC_CAPABILITIES,
                checkpoint_available=(
                    checkpoint.is_enabled() and checkpoint.is_git_repo(req.workdir)
                ),
                name=opened.name,
                display_title=display_title,
                title_source=title_source,
                connection_id=launch.connection_id if launch else None,
                connection_identity_version=(
                    launch.connection_identity_version if launch else None
                ),
                connection_name=launch.connection_name if launch else None,
                connection_kind=launch.kind.value if launch else None,
                configuration_capability_version=(
                    launch.capability_version if launch else None
                ),
                configuration_capability_source=(
                    launch.capability_source if launch else None
                ),
            )
        except BaseException as exc:
            self._lifecycle.abort_created(Runtime.CLAUDE_CODE, opened.sid, exc)
            raise
        self._lifecycle.commit_created(binding)
        if launch is not None:
            self._pending_last_choices[binding.session_id] = launch
        self._persist_native_title(binding)
        if req.session_kind == "user":
            self._active_id = opened.sid
        return binding

    @staticmethod
    def _rollback_claude_connection_settings(
        settings_path: str | Path | None,
        owned: bool,
        close_callback: Callable[[], None] | None,
    ) -> None:
        """回滚尚未交给 CCHost 持有的私有 settings 和代理租约。"""

        if owned and settings_path is not None:
            Path(settings_path).unlink(missing_ok=True)
        if close_callback is not None:
            close_callback()

    def _create_codex(
        self,
        req: CreateAgentSessionRequest,
        launch: RuntimeLaunchConfiguration | None,
        memory_mcp_enabled: bool,
        bootstrap_context: str | None,
    ) -> SessionBinding:
        """``resume_from`` 只登记原生 thread，首次 turn 才执行恢复。"""

        if not self.runtime_available(Runtime.CODEX):
            raise RuntimeUnavailableError("Codex CLI 未安装")
        self._refuse_on_trowel_mcp_collision(req.workdir)
        prepared = prepare_codex_session(
            req,
            session_id_factory=lambda: uuid.uuid4().hex,
            permission_presets=_CODEX_PERMISSION_PRESETS,
            fingerprint=_injection_fingerprint,
            resource_registry=self._resource_registry,
            memory_mcp_enabled=memory_mcp_enabled,
            bootstrap_context=bootstrap_context,
        )
        sid = prepared.session_id
        display_title, title_source = self._initial_title(req)
        if self._codex is not None:
            if launch is None:
                self._codex.register(prepared.session)
            else:
                self._codex.register(prepared.session, launch=launch)
        try:
            binding = make_binding(
                session_id=sid,
                runtime=Runtime.CODEX,
                native_session_id=req.resume_from,
                workdir=req.workdir,
                model=req.model,
                effort=req.effort,
                permission=None,
                memory_enabled=req.memory_enabled,
                memory_mcp_enabled=memory_mcp_enabled,
                profile_enabled=req.profile_enabled,
                self_enabled=req.self_enabled,
                session_kind=req.session_kind,
                memory_eligibility=req.memory_eligibility,
                agent_mcp_enabled=req.agent_mcp_enabled,
                parent_session_id=req.parent_session_id,
                delegation_depth=req.delegation_depth,
                owner_ref=req.owner_ref,
                capabilities=CODEX_CAPABILITIES,
                checkpoint_available=False,
                name=self._display_name(req.workdir),
                permission_preset=prepared.permission_preset,
                injection_hash=prepared.injection_hash,
                declared_mcp_roster=prepared.declared_mcp_roster,
                display_title=display_title,
                title_source=title_source,
                connection_id=launch.connection_id if launch else None,
                connection_identity_version=(
                    launch.connection_identity_version if launch else None
                ),
                connection_name=launch.connection_name if launch else None,
                connection_kind=launch.kind.value if launch else None,
                configuration_capability_version=(
                    launch.capability_version if launch else None
                ),
                configuration_capability_source=(
                    launch.capability_source if launch else None
                ),
            )
        except BaseException as exc:
            self._lifecycle.abort_created(Runtime.CODEX, sid, exc)
            raise
        self._lifecycle.commit_created(binding)
        if launch is not None:
            self._pending_last_choices[binding.session_id] = launch
        self._persist_native_title(binding)
        if req.session_kind == "user":
            self._active_id = sid
        return binding

    def _refuse_on_trowel_mcp_collision(self, workdir: str) -> None:
        """任一受检配置层存在同名 MCP 时都无法保证 Trowel roster 隔离。"""

        from trowel_py.agent_mcp.launch import AGENT_MCP_SERVER_NAME
        from trowel_py.codex_host.mcp_isolation import find_conflicting_mcp_server
        from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME

        for server_name in (
            TROWEL_NOTE_SEARCH_SERVER_NAME,
            AGENT_MCP_SERVER_NAME,
        ):
            conflict = find_conflicting_mcp_server(
                server_name,
                codex_home=self._codex_config_home,
                workdir=workdir,
            )
            if conflict is not None:
                raise SessionConflictError(
                    f"a Codex MCP server named {conflict.server_name!r} is "
                    f"already declared in {conflict.config_path}; trowel "
                    f"cannot guarantee its managed MCP roster. Rename or remove "
                    f"that entry and retry."
                )

    def _display_name(self, workdir: str) -> str:
        """根据同目录当前连接的用户会话分配最小可用临时编号。"""

        occupied_names = (
            binding.name
            for binding in self._store.list_all()
            if binding.workdir == workdir
            and binding.session_kind == "user"
            and self._live_status(binding)[0]
        )
        return next_session_display_name(workdir, occupied_names)

    def get(self, session_id: str) -> SessionBinding | None:
        """读取指定 Trowel 会话的持久化记录，找不到时返回 None。"""

        return self._store.get(session_id)

    async def list_codex_models(self) -> list[dict[str, Any]]:
        """读取当前 Codex 提供的全部可见模型及其思考强度选项。

        Returns:
            Codex 模型列表。每项包含模型 ID、显示名称、说明、默认思考强度和支持的
            思考强度；顺序与 Codex 返回结果一致。

        Raises:
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
        """

        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        return await self._codex.list_models()

    async def list_codex_models_for_launch(
        self, launch: RuntimeLaunchConfiguration
    ) -> list[dict[str, Any]]:
        """从某条冻结 Codex 连接的独立 app-server 读取模型目录。

        Args:
            launch: 含 provider 和认证边界的连接专属启动配置。

        Returns:
            该连接可见的原生模型顺序、默认强度和完整强度集合。

        Raises:
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
        """

        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        reader = getattr(self._codex, "list_models_for_launch", None)
        if reader is None:
            return await self._codex.list_models()
        return await reader(launch)

    async def read_codex_account_for_launch(
        self, launch: RuntimeLaunchConfiguration
    ) -> dict[str, str | None]:
        """读取一项 Official 供应商的 Codex 原生账号摘要。"""

        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        reader = getattr(self._codex, "read_account_for_launch", None)
        if reader is None:
            raise RuntimeUnavailableError("codex account API unavailable")
        return await reader(launch)

    async def start_codex_account_login_for_launch(
        self, launch: RuntimeLaunchConfiguration
    ) -> dict[str, str]:
        """为一项 Official 供应商启动 Codex 原生 device-code 登录。"""

        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        starter = getattr(self._codex, "start_account_login_for_launch", None)
        if starter is None:
            raise RuntimeUnavailableError("codex account API unavailable")
        return await starter(launch)

    async def release_codex_launch(self, launch: RuntimeLaunchConfiguration) -> bool:
        """删除供应商前释放没有会话引用的连接 manager。"""

        if self._codex is None:
            return True
        releaser = getattr(self._codex, "release_launch", None)
        if releaser is None:
            return True
        return bool(await releaser(launch))

    async def list_history(
        self,
        workdir: str,
        *,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """读取指定工作目录的 Claude Code 与 Codex 历史会话，并按更新时间分页。

        未配置 Codex 会话管理器时只返回 Claude Code 历史会话。

        Args:
            workdir: 要查询历史会话的工作目录。
            limit: 本页最多返回的会话数，范围为 1 到 100。
            cursor: 上一页返回的下一页游标；首次查询时传入 None。

        Returns:
            历史会话列表和下一页游标。每条记录包含运行工具、Claude Code 会话 ID
            或 Codex thread ID、标题和更新时间；没有下一页时游标为 None。

        Raises:
            InvalidSessionRequestError: limit 超出范围或分页游标无效。
        """

        from trowel_py.agent_host.history import (
            HistoryCursorError,
            decode_history_cursor,
            merge_history_page,
            scan_cc_history,
        )

        if limit < 1 or limit > 100:
            raise InvalidSessionRequestError("history limit must be between 1 and 100")
        try:
            offset = decode_history_cursor(cursor) if cursor is not None else 0
        except HistoryCursorError as exc:
            raise InvalidSessionRequestError(str(exc)) from exc
        required = offset + limit + 1
        cc_non_user_ids = self._non_user_identities.ids(Runtime.CLAUDE_CODE)
        codex_non_user_ids = self._non_user_identities.ids(Runtime.CODEX)
        cc_summaries = await asyncio.to_thread(
            scan_cc_history,
            workdir,
            limit=required,
            excluded_ids=cc_non_user_ids,
        )
        codex_threads: list[dict[str, Any]] = []
        if self._codex is not None:
            codex_threads = await self._codex.list_threads(
                cwd=workdir,
                limit=required,
                excluded_ids=codex_non_user_ids,
            )
        rows, next_cursor = merge_history_page(
            cc_summaries,
            codex_threads,
            offset=offset,
            limit=limit,
        )
        for row in rows:
            try:
                runtime = Runtime(str(row["runtime"]))
                native_session_id = str(row["native_session_id"])
            except (KeyError, ValueError):
                continue
            saved = self._title_store.get(runtime, native_session_id)
            if saved is not None:
                row["title"] = saved.title
                row["title_source"] = saved.source
            else:
                row["title_source"] = "native"
        return rows, next_cursor

    async def history(self, session_id: str) -> list[dict[str, Any]]:
        """读取指定会话的历史事件，并转换为通用 AgentEvent 格式。

        每次读取都从序号 1 重新编号。会话记录中尚无 Claude Code 会话 ID 或 Codex
        thread ID 时返回空列表。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            按历史顺序排列的消息、工具调用和状态等事件。

        Raises:
            SessionNotFoundError: 找不到对应的 Trowel 会话。
            RuntimeUnavailableError: 该会话属于 Codex，但未配置 Codex 会话管理器。
        """

        binding = self._require(session_id)
        native_session_id = binding.native_session_id
        if not native_session_id:
            return []
        if binding.runtime is Runtime.CLAUDE_CODE:
            from trowel_py.cc_host.history import parse_history

            events = await asyncio.to_thread(
                parse_history, binding.workdir, native_session_id
            )
            cc_adapter = ClaudeCodeEventAdapter(session_id)
            return [
                cc_adapter.wrap(event.model_dump()).model_dump(by_alias=True)
                for event in events
            ]
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        from trowel_py.codex_host.history import events_from_thread

        thread = await self._codex.read_thread(native_session_id)
        turn_event_overrides = {}
        if self._codex_history_root is not None:
            from trowel_py.memory.codex_journal import read_thread_journal_events

            try:
                turn_event_overrides = await asyncio.to_thread(
                    read_thread_journal_events,
                    self._codex_history_root,
                    native_session_id,
                    session_id=session_id,
                )
            except Exception:  # noqa: BLE001 - 历史日志异常时保留原生回放能力。
                _log.warning(
                    "Codex normalized history unavailable for thread=%s; "
                    "falling back to thread/read",
                    native_session_id,
                    exc_info=True,
                )
        codex_adapter = CodexEventAdapter(session_id)
        envelopes = []
        for event in events_from_thread(
            session_id,
            thread,
            turn_event_overrides=turn_event_overrides,
        ):
            envelope = codex_adapter.wrap(event)
            if envelope is not None:
                envelopes.append(envelope.model_dump(by_alias=True))
        return envelopes

    async def child_history(
        self, session_id: str, child_thread_id: str
    ) -> list[dict[str, Any]]:
        """读取指定 Codex 子 Agent thread 自己产生的历史事件。

        只有确认该 thread 最终隶属于当前会话的主 thread 后才返回。Codex 子 thread 中
        从父级继承的轮次会被排除，避免重复显示父级历史。

        Args:
            session_id: 父级 Trowel 会话 ID。
            child_thread_id: 要读取的 Codex 子 Agent thread ID。

        Returns:
            子 Agent 自己产生并从序号 1 重新编号的 AgentEvent 列表。

        Raises:
            SessionNotFoundError: 找不到父级会话或其主 thread ID。
            SessionOperationError: 父级会话不是 Codex 会话。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionAccessError: 指定 thread 不是该会话的子级，或父级关系中出现循环。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise SessionOperationError("subagent history is only available for Codex")
        root_thread_id = binding.native_session_id
        if not root_thread_id:
            raise SessionNotFoundError(f"session {session_id} has no native thread")
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        if child_thread_id == root_thread_id:
            raise SessionAccessError("requested thread is not a child of this session")

        thread = await self._codex.read_thread(child_thread_id)
        current = thread
        visited = {child_thread_id}
        inherited_turn_ids: set[str] = set()
        while True:
            parent_thread_id = current.get("parentThreadId")
            if not isinstance(parent_thread_id, str) or not parent_thread_id:
                raise SessionAccessError(
                    "subagent thread does not belong to this session"
                )
            if parent_thread_id in visited:
                raise SessionAccessError("subagent parent chain contains a cycle")
            visited.add(parent_thread_id)
            parent = await self._codex.read_thread(parent_thread_id)
            inherited_turn_ids.update(_native_turn_ids(parent))
            if parent_thread_id == root_thread_id:
                break
            current = parent

        from trowel_py.codex_host.history import events_from_thread

        adapter = CodexEventAdapter(session_id)
        envelopes = []
        child_only = dict(thread)
        turns = thread.get("turns")
        if isinstance(turns, list):
            child_only["turns"] = [
                turn
                for turn in turns
                if not isinstance(turn, Mapping)
                or turn.get("id") not in inherited_turn_ids
            ]
        for event in events_from_thread(
            session_id, child_only, include_turn_started=True
        ):
            envelope = adapter.wrap(event)
            if envelope is not None:
                envelopes.append(envelope.model_dump(by_alias=True))
        return envelopes

    def _require(self, session_id: str) -> SessionBinding:
        """根据 Trowel 会话 ID 读取对应的会话记录；找不到时抛出 SessionNotFoundError。"""

        binding = self._store.get(session_id)
        if binding is None:
            raise SessionNotFoundError(f"session {session_id} not found")
        return binding

    def list_active(self) -> tuple[list[dict[str, Any]], str | None]:
        """列出用户直接管理的会话，并补充当前连接和轮次运行状态。

        Returns:
            用户会话记录列表与当前选中的用户会话 ID。每条记录在原有字段外增加
            connected 和 running；当前没有选中用户会话时，会话 ID 为 None。
        """

        items: list[dict[str, Any]] = []
        for binding in self._store.list_all():
            if binding.session_kind != "user":
                continue
            item = binding.to_dict()
            connected, running = self._live_status(binding)
            item["connected"] = connected
            item["running"] = running
            item.update(self._lifecycle_snapshot(binding, running=running))
            items.append(item)
        user_ids = {str(item["session_id"]) for item in items}
        active_id = self._active_id if self._active_id in user_ids else None
        return items, active_id

    @property
    def live_generation(self) -> str:
        """返回当前 Agent Host 进程的实时事件流代次。"""

        return self._live_generation

    def _lifecycle_snapshot(
        self, binding: SessionBinding, *, running: bool
    ) -> dict[str, object]:
        """生成 renderer 对账使用的资源与根 turn 快照。

        Args:
            binding: 当前仍存在的用户会话记录。
            running: runtime port 现场确认的未结束 turn 事实。

        Returns:
            资源状态、根 turn 状态和身份、状态代次及最近事件序号。
        """

        close_task = self._session_close_tasks.get(binding.session_id)
        if close_task is not None and not close_task.done():
            resource_state = "closing"
        elif binding.session_id in self._closing_session_ids:
            resource_state = "needs_reconcile"
        else:
            resource_state = "connected"
        observed_turn_state = self._last_root_turn_states.get(
            binding.session_id, "idle"
        )
        unfinished_states = {
            "starting",
            "running",
            "awaiting_input",
        }
        terminal_states = {"completed", "failed", "interrupted"}
        if observed_turn_state in terminal_states:
            # 原生任务在 terminal 发布后才释放容量令牌。这个极短窗口里
            # runtime 仍可能报告 running，但业务根 turn 已有更强的终态事实。
            turn_state = observed_turn_state
        elif running and observed_turn_state in unfinished_states:
            turn_state = observed_turn_state
        elif running:
            turn_state = "running"
        elif observed_turn_state in unfinished_states:
            # runtime 已确认没有在途 turn，但 Host 没观察到对应 terminal，说明
            # 原生事件链异常结束。不能继续把 snapshot 宣称为 running。
            turn_state = "failed"
        else:
            turn_state = observed_turn_state
        return {
            "resource_state": resource_state,
            "turn_state": turn_state,
            "current_turn_id": self._current_root_turn_ids.get(binding.session_id),
            "state_generation": self._session_state_generations.get(
                binding.session_id, 1
            ),
            "last_event_seq": self._last_event_sequences.get(binding.session_id),
        }

    def _touch_session_state(self, session_id: str) -> int:
        """递增指定会话的 snapshot 代次并返回新值。"""

        generation = self._session_state_generations.get(session_id, 0) + 1
        self._session_state_generations[session_id] = generation
        return generation

    def _live_status(self, binding: SessionBinding) -> tuple[bool, bool]:
        """计算会话列表中的 connected 和 running 状态。

        两种 runtime 的 connected 都表示逻辑会话仍登记且可接受下一轮。Claude Code
        当前子进程在中断后可以退出，下一条消息会按原生会话 ID 恢复，因此不能把进程
        存活误当成会话连接。running 表示当前有轮次正在执行。

        Args:
            binding: 要检查的 Trowel 会话记录。

        Returns:
            connected 和 running 组成的二元组，顺序为 connected、running。
        """

        state = self._capacity.live_state(binding)
        detached = self._detached_turn_tasks.get(binding.session_id)
        detached_running = detached is not None and not detached.done()
        return (
            state.connected,
            self._capacity.has_in_flight_turn(binding) or detached_running,
        )

    def _reserve_turn(self, binding: SessionBinding) -> object:
        """预留对应会话池的在跑名额，并把容量拒绝转换为 Hub 冲突错误。"""

        if binding.session_id in self._closing_session_ids:
            raise SessionConflictError(
                f"session {binding.session_id} 正在关闭，不能启动新轮次"
            )
        try:
            return self._capacity.reserve_turn(binding)
        except (CapacityConflictError, CapacityLimitError) as exc:
            raise SessionConflictError(str(exc)) from exc

    def activate(self, session_id: str) -> str:
        """将指定用户会话设为当前选中的会话。

        选择 Claude Code 会话时，同时更新旧版 Claude Code 接口保存的当前会话 ID，
        保证两个接口状态一致。

        Args:
            session_id: 要选中的 Trowel 会话 ID。

        Returns:
            当前选中的 Trowel 会话 ID。

        Raises:
            SessionNotFoundError: 找不到对应的 Trowel 会话。
            SessionOperationError: 指定会话是后台委派会话，不能进入用户工作台。
        """

        binding = self._require(session_id)
        if binding.session_kind != "user":
            raise SessionOperationError(
                "delegate session cannot become the current user session"
            )
        self._active_id = session_id
        if binding.runtime is Runtime.CLAUDE_CODE:
            from trowel_py.cc_host import routes as cc_routes

            cc_routes.set_active_session_id(session_id)
        return session_id

    def patch(self, session_id: str, **fields: Any) -> None:
        """检查会话修改请求是否试图更换运行工具。

        此方法只检查 runtime，不保存其他字段；模型、思考强度和权限由各自的方法处理。

        Args:
            session_id: 要修改的 Trowel 会话 ID。
            fields: 修改请求中的字段；只有 runtime 会在此处检查。

        Raises:
            SessionNotFoundError: 找不到对应的 Trowel 会话。
            RuntimeFrozenError: 请求把会话改为另一种运行工具。
        """

        binding = self._require(session_id)
        new_runtime = fields.get("runtime")
        if new_runtime is not None and new_runtime != binding.runtime.value:
            raise RuntimeFrozenError(
                f"runtime is frozen at create (C-1): cannot change "
                f"{binding.runtime.value} -> {new_runtime}"
            )

    async def update_codex_settings(
        self,
        session_id: str,
        *,
        model: str | None,
        effort: str | None,
    ) -> dict[str, Any]:
        """为指定 Codex 会话选择并暂存下一轮使用的模型和思考强度。

        请求值会根据 Codex 当前提供的模型列表进行校验。思考强度不受所选模型支持时，
        改用该模型的默认值；设置只有在下一轮被 Codex 接受后才写入 Trowel 会话记录。

        Args:
            session_id: 要修改的 Trowel 会话 ID。
            model: 下一轮请求使用的模型；未指定时沿用已有配置。
            effort: 下一轮请求使用的思考强度；未指定时沿用已有配置。

        Returns:
            最终选定的 model、effort，以及是否因不兼容而调整过 effort。

        Raises:
            SessionNotFoundError: 找不到对应的 Trowel 会话或 Codex 会话。
            SessionOperationError: 会话不是 Codex 会话，模型不存在，或没有可用的思考强度。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionConflictError: 当前会话状态不允许修改下一轮设置。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise SessionOperationError("model/effort PATCH is Codex-only")
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        catalog = await self._codex.list_models()
        current_native = getattr(session, "binding", None)
        selection_error: str | None = None
        try:
            selected = select_turn_settings(
                catalog,
                requested_model=model,
                stored_model=binding.model,
                native_model=getattr(current_native, "model", None),
                configured_model=session.config.model,
                requested_effort=effort,
                stored_effort=binding.effort,
                native_effort=getattr(current_native, "reasoning_effort", None),
                configured_effort=session.config.effort,
            )
        except (UnknownModelError, NoUsableEffortError) as exc:
            selection_error = str(exc)
        if selection_error is not None:
            raise SessionOperationError(selection_error)
        from trowel_py.codex_host import TurnConflictError

        try:
            session.queue_turn_settings(selected.model, selected.effort)
        except TurnConflictError as exc:
            raise SessionConflictError(str(exc)) from exc
        return {
            "model": selected.model,
            "effort": selected.effort,
            "adjusted": selected.adjusted,
        }

    async def update_codex_permission(
        self,
        session_id: str,
        *,
        permission_preset: str,
    ) -> dict[str, Any]:
        """更新指定 Codex 会话后续轮次使用的权限模式。

        新权限同时用于下一轮请求和 Codex 重连。Trowel 会话记录会先于内存中的 Codex
        会话更新，保存失败时不会出现只修改一半的状态。接口不允许切换到 "follow"，
        因为它无法可靠撤销当前会话中已经生效的明确权限。

        Args:
            session_id: 要修改的 Trowel 会话 ID。
            permission_preset: 新权限模式，可选 "read-only"、"workspace-write" 或
                "danger-full-access"。

        Returns:
            包含最终 permission_preset 的字典。

        Raises:
            SessionNotFoundError: 找不到对应的 Trowel 会话或 Codex 会话。
            SessionOperationError: 会话不是 Codex 会话，或权限模式无效。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionConflictError: 当前会话状态不允许修改权限。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise SessionOperationError("permission PATCH is Codex-only")
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        if permission_preset == "follow":
            raise SessionOperationError(
                "permission PATCH cannot switch to 'follow'; "
                "follow has no sticky revoke semantics"
            )
        try:
            approval, sandbox = _CODEX_PERMISSION_PRESETS[permission_preset]
        except KeyError as exc:
            raise SessionOperationError(
                f"unknown permission preset {permission_preset!r}"
            ) from exc
        session = self._codex.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        if not session.can_queue_permission_override:
            # 先做只读检查，避免持久化已写盘后 queue 才抛错的部分成功。
            raise SessionConflictError(
                f"session {session_id} cannot change permission in state "
                f"{session.state.name}"
            )
        updated = replace(binding, permission_preset=permission_preset)
        # 持久化必须先于内存改动：put 失败时 queue/apply 都未执行，
        # session 的 pending override 与 live config 保持原状。
        self._store.put(updated)
        session.queue_permission_override(approval=approval, sandbox=sandbox)
        session.apply_permission_override(approval=approval, sandbox=sandbox)
        return {"permission_preset": permission_preset}

    def _require_codex_runtime(self) -> Any:
        """返回已配置的 Codex 会话管理器；未配置时抛出 RuntimeUnavailableError。"""

        codex = self._codex
        if codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        return codex

    def _require_codex_session(self, session_id: str) -> Any:
        """读取 Trowel 会话 ID 对应的已登记 Codex 会话。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            Codex 会话管理器中登记的会话对象。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该 Trowel 会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise SessionOperationError("Goal is Codex-only")
        session = self._require_codex_runtime().get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        return session

    def require_codex_session(self, session_id: str) -> None:
        """在创建 Codex 事件流响应前，确认会话由 Codex 运行且仍登记在管理器中。

        这样可以在 HTTP 200 响应发出前返回正确的错误状态。

        Args:
            session_id: Trowel 会话 ID。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该 Trowel 会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
        """

        self._require_codex_session(session_id)

    async def get_codex_goal(self, session_id: str) -> dict[str, Any] | None:
        """读取 Codex 会话当前设置的 Goal。

        读取前会确认对应的 Codex thread 已连接，并将实际采用的 thread ID、模型、
        思考强度和权限写回 Trowel 会话记录。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            Codex 返回的目标内容；尚未设置目标时返回 None。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该 Trowel 会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionConflictError: Codex 当前状态不允许连接对应的 thread。
            RuntimeTurnError: 连接 thread、保存会话信息或读取目标失败。
        """

        session = self._require_codex_session(session_id)
        codex = self._require_codex_runtime()
        try:
            await self._prepare_codex_goal_session(session_id, session)
            return await codex.get_goal(session)
        except TurnConflictError as exc:
            raise SessionConflictError(str(exc)) from exc
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一为 runtime 失败边界。
            raise RuntimeTurnError(f"codex goal get failed: {exc}") from exc

    async def list_codex_commands(self, session_id: str) -> list[dict[str, Any]]:
        """列出当前 Codex CLI 版本已验证可用的会话命令。

        这些命令由 Trowel 单独处理，不会作为普通消息发送给 Codex；CLI 版本未经验证时
        返回空列表。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            可用命令列表。每项包含命令名称、说明、来源、对应操作，以及轮次运行期间
            能否使用。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该 Trowel 会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            RuntimeTurnError: 启动 Codex 或读取 CLI 版本失败。
        """

        self._require_codex_session(session_id)
        try:
            return await self._require_codex_runtime().list_commands()
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeTurnError(f"codex command roster failed: {exc}") from exc

    async def compact_codex(self, session_id: str) -> None:
        """请求 Codex 压缩当前 thread 的上下文。

        此操作不发送普通用户消息。函数在 Codex 接受请求后返回，不等待压缩完成；后续
        进度和结果由会话事件流报告。

        Args:
            session_id: Trowel 会话 ID。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该 Trowel 会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionConflictError: 当前有其他轮次正在运行。
            RuntimeTurnError: 连接 thread、保存会话信息或启动压缩失败。
        """

        binding = self._require(session_id)
        reservation = self._reserve_turn(binding)
        try:
            session = self._require_codex_session(session_id)
            codex = self._require_codex_runtime()
            try:
                await codex.compact(
                    session,
                    before_start=lambda attached: self._writeback_codex_before_turn(
                        session_id, attached
                    ),
                )
            except TurnConflictError as exc:
                raise SessionConflictError(str(exc)) from exc
            except SessionHubError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RuntimeTurnError(f"codex compact failed: {exc}") from exc
        finally:
            self._capacity.release_turn(reservation)

    async def start_codex_review(
        self, session_id: str, target: dict[str, Any]
    ) -> dict[str, str]:
        """按指定范围启动 Codex 的代码审查。

        Args:
            session_id: Trowel 会话 ID。
            target: 要审查的内容，可以是未提交的改动、与指定基础分支的差异、某个提交
                或自定义审查要求。

        Returns:
            审查使用的 Codex thread ID 和本次审查的轮次 ID。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionConflictError: 当前有其他轮次正在运行。
            RuntimeTurnError: 启动审查失败。
        """

        binding = self._require(session_id)
        reservation = self._reserve_turn(binding)
        try:
            session = self._require_codex_session(session_id)
            codex = self._require_codex_runtime()
            try:
                result = await codex.start_review(
                    session,
                    target,
                    before_start=lambda attached: self._writeback_codex_before_turn(
                        session_id, attached
                    ),
                )
                self._writeback_codex_native(session_id, session)
                return result
            except TurnConflictError as exc:
                raise SessionConflictError(str(exc)) from exc
            except SessionHubError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RuntimeTurnError(f"codex review failed: {exc}") from exc
        finally:
            self._capacity.release_turn(reservation)

    async def set_codex_goal(
        self,
        session_id: str,
        *,
        objective: str | None,
        status: str | None,
        token_budget: int | None,
        token_budget_supplied: bool,
    ) -> dict[str, Any]:
        """创建 Codex Goal，或更新已有 Goal 中指定的字段。

        Args:
            session_id: Trowel 会话 ID。
            objective: Goal 要完成的内容；None 表示不修改。
            status: Goal 当前的执行状态；None 表示不修改。
            token_budget: Goal 最多可以使用的 token 数量。
            token_budget_supplied: 是否更新 token 数量限制。False 表示保留原值；True
                表示使用 token_budget，其中 None 表示取消限制。

        Returns:
            Codex 返回的完整 Goal。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            RuntimeTurnError: 创建或更新 Goal 失败。
        """

        session = self._require_codex_session(session_id)
        codex = self._require_codex_runtime()
        try:
            await self._prepare_codex_goal_session(session_id, session)
            return await codex.set_goal(
                session,
                objective=objective,
                status=status,
                token_budget=token_budget,
                token_budget_supplied=token_budget_supplied,
            )
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001 - 保留上游错误文本供界面诊断。
            raise RuntimeTurnError(f"codex goal set failed: {exc}") from exc

    async def clear_codex_goal(self, session_id: str) -> bool:
        """清除 Codex 会话当前设置的 Goal。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            是否成功清除了 Goal。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的 Codex 会话。
            SessionOperationError: 该会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            RuntimeTurnError: 清除 Goal 失败。
        """

        session = self._require_codex_session(session_id)
        codex = self._require_codex_runtime()
        try:
            await self._prepare_codex_goal_session(session_id, session)
            return await codex.clear_goal(session)
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeTurnError(f"codex goal clear failed: {exc}") from exc

    async def _prepare_codex_goal_session(self, session_id: str, session: Any) -> None:
        """在读写 Goal 前连接对应的 Codex thread，并保存实际会话信息。

        Args:
            session_id: Trowel 会话 ID。
            session: Codex 会话管理器中登记的会话对象。
        """

        await self._require_codex_runtime().attach(
            session,
            before_commit=lambda attached: self._writeback_codex_before_turn(
                session_id, attached
            ),
        )

    def validate_resume(
        self,
        runtime: Runtime,
        native_session_id: str | None,
        *,
        memory_enabled: bool | None = None,
        profile_enabled: bool | None = None,
        self_enabled: bool | None = None,
    ) -> None:
        """恢复已有原生 id 时保持 runtime 与显式注入开关不变。

        开关为 None 表示调用方未指定，兼容旧请求并跳过该项校验。
        """

        if native_session_id is None:
            return
        frozen_conditions: list[SessionBinding | FrozenSessionConfiguration] = [
            binding
            for binding in self._store.list_all()
            if binding.native_session_id == native_session_id
        ]
        for candidate_runtime in Runtime:
            archived = self._configuration_archive.get(
                candidate_runtime, native_session_id
            )
            if archived is not None:
                frozen_conditions.append(archived)
        for frozen in frozen_conditions:
            if frozen.runtime is not runtime:
                raise CrossRuntimeResumeError(
                    f"native session {native_session_id!r} is bound to "
                    f"{frozen.runtime.value}; cannot resume as "
                    f"{runtime.value} (C-2)"
                )
            if memory_enabled is not None and frozen.memory_enabled != memory_enabled:
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"memory_enabled={frozen.memory_enabled}; cannot resume "
                    f"as memory_enabled={memory_enabled} (C-2)"
                )
            if (
                profile_enabled is not None
                and frozen.profile_enabled != profile_enabled
            ):
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"profile_enabled={frozen.profile_enabled}; cannot "
                    f"resume as profile_enabled={profile_enabled} (C-2)"
                )
            if self_enabled is not None and frozen.self_enabled != self_enabled:
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"self_enabled={frozen.self_enabled}; cannot resume as "
                    f"self_enabled={self_enabled} (C-2)"
                )

    async def interrupt(self, session_id: str) -> None:
        """请求中断会话当前正在运行的轮次。

        没有正在运行的轮次时不执行任何操作；方法返回不代表轮次已经结束。

        Args:
            session_id: Trowel 会话 ID。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或对应的运行中会话。
            RuntimeUnavailableError: 会话由 Codex 运行，但未配置 Codex 会话管理器。
        """

        binding = self._require(session_id)
        if binding.runtime is Runtime.CLAUDE_CODE:
            host = self._cc_registry.get(session_id)
            if host is None:
                raise SessionNotFoundError(f"cc session {session_id} not live")
            await host.interrupt()
            return
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        await self._codex.interrupt(session)

    async def cancel_elicitation(self, session_id: str) -> bool:
        """拒绝 CC 会话当前待回答的 AskUserQuestion。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            deny 控制消息已经写入时为 True。

        Raises:
            SessionNotFoundError: 找不到会话或 CC host 不在线。
            SessionOperationError: 会话不是 Claude Code，或当前没有可取消提问。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CLAUDE_CODE:
            raise SessionOperationError("only CC sessions support elicitation")
        host = self._cc_registry.get(session_id)
        if host is None:
            raise SessionNotFoundError(f"cc session {session_id} not live")
        cancelled = await host.cancel_elicit()
        if not cancelled:
            raise SessionOperationError("CC elicitation is no longer pending")
        return True

    def answer_request(
        self, session_id: str, request_id: str, decision: str
    ) -> dict[str, Any]:
        """把用户对命令执行或文件修改请求的决定交回正在等待答复的 Codex。

        Args:
            session_id: Trowel 会话 ID。
            request_id: 要回答的请求 ID。
            decision: 选择的处理方式，必须是该请求提供的选项之一。

        Returns:
            回答后的完整请求信息，包括请求状态和最终选择。

        Raises:
            SessionNotFoundError: 找不到 Trowel 会话记录或指定请求。
            SessionOperationError: 该会话由 Claude Code 运行，或选择不在允许范围内。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionAccessError: 请求属于另一个会话。
            SessionConflictError: 请求已经被回答、过期或关闭。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise SessionOperationError(
                "Codex pending-request answers cannot use the CC contract"
            )
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        try:
            request = self._codex.answer_request(session_id, request_id, decision)
        except PendingRequestNotFoundError as exc:
            raise SessionNotFoundError(str(exc)) from exc
        except PendingRequestOwnershipError as exc:
            raise SessionAccessError(str(exc)) from exc
        except PendingRequestDecisionError as exc:
            raise SessionOperationError(str(exc)) from exc
        except PendingRequestConflictError as exc:
            raise SessionConflictError(str(exc)) from exc
        return request.to_payload()

    def decline_request(self, session_id: str, request_id: str) -> dict[str, Any]:
        """立即拒绝 Codex 内部会话的待处理审批，不等待公开 UI。

        Args:
            session_id: Trowel 会话 ID。
            request_id: Codex 待处理请求 ID。

        Returns:
            自动拒绝后的完整请求 payload。
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise SessionOperationError("only Codex sessions support approvals")
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        try:
            request = self._codex.decline_request(session_id, request_id)
        except PendingRequestNotFoundError as exc:
            raise SessionNotFoundError(str(exc)) from exc
        except PendingRequestOwnershipError as exc:
            raise SessionAccessError(str(exc)) from exc
        except PendingRequestConflictError as exc:
            raise SessionConflictError(str(exc)) from exc
        return request.to_payload()

    def list_requests(self, session_id: str) -> list[dict[str, Any]]:
        """返回保留中的 Codex 请求，使短暂断线不会丢失待决策状态。"""

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            return []
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        return [
            request.to_payload() for request in self._codex.list_requests(session_id)
        ]

    async def close_result(
        self,
        session_id: str,
        *,
        delete_binding: bool = True,
    ) -> SessionCloseResult:
        """收敛一个会话并返回 closed、needs_reconcile 或 not_found。

        Args:
            session_id: 要关闭的 Trowel 会话 ID。
            delete_binding: 用户关闭时删除 binding；应用退出时保留恢复入口。

        Returns:
            包含剩余资源数量和类型的显式关闭结果。
        """

        if delete_binding:
            closed_result = self._closed_session_results.get(session_id)
            if closed_result is not None:
                return closed_result
        task = self._session_close_tasks.get(session_id)
        if task is None:
            task = asyncio.create_task(
                self._close_session_once(
                    session_id,
                    delete_binding=delete_binding,
                ),
                name=f"agent-session-close:{session_id}",
            )
            self._session_close_tasks[session_id] = task
            task.add_done_callback(
                lambda completed, key=session_id: self._discard_session_close_task(
                    key,
                    completed,
                )
            )
        result = await asyncio.shield(task)
        if (
            delete_binding
            and result.status == "closed"
            and self._store.get(session_id) is not None
        ):
            # 应用 drain 可能先创建“保留 binding”的共享任务。用户删除随后加入时，
            # 复用已完成的 runtime 清理，再单独提交持久删除和 Memory 请求。
            self._discard_session_close_task(session_id, task)
            return await self.close_result(session_id, delete_binding=True)
        return result

    def _discard_session_close_task(
        self,
        session_id: str,
        completed: asyncio.Task[SessionCloseResult],
    ) -> None:
        """关闭任务结束后移除临时去重记录，同时取走无人等待的异常。"""

        if self._session_close_tasks.get(session_id) is completed:
            self._session_close_tasks.pop(session_id, None)
        if not completed.cancelled():
            completed.exception()

    async def _close_session_once(
        self,
        session_id: str,
        *,
        delete_binding: bool,
    ) -> SessionCloseResult:
        """执行一次真实会话关闭；并发合并和成功终态缓存由外层负责。"""

        binding = self._store.get(session_id)
        if binding is None:
            return SessionCloseResult.not_found()
        if binding.runtime not in self._runtime_ports:
            raise RuntimeUnavailableError(f"{binding.runtime.value} host unavailable")
        self._closing_session_ids.add(session_id)
        try:
            review_requester = None
            if (
                delete_binding
                and binding.session_kind == "user"
                and binding.memory_eligibility
                and binding.memory_enabled
                and self._session_review_requester is not None
            ):

                def persist_review_request() -> None:
                    """在 binding 删除前持久登记当前用户会话的 Memory review。"""

                    if self._session_review_requester is not None:
                        self._session_review_requester(binding)

                review_requester = persist_review_request
            runtime_result = await self._lifecycle.close(
                binding,
                require_idle=False,
                busy_message="",
                before_runtime_close=lambda: self._stop_session_event_delivery(
                    session_id
                ),
                before_binding_delete=review_requester,
                delete_binding=delete_binding,
            )
        except (CapacityConflictError, SessionInFlightError) as exc:
            raise SessionConflictError(str(exc)) from exc
        result = SessionCloseResult.from_runtime(runtime_result)
        if result.status != "closed":
            return result
        self._closing_session_ids.discard(session_id)
        # 删除 adapter，避免复用 id 继承旧序号。
        self._cc_adapters.pop(session_id, None)
        self._codex_adapters.pop(session_id, None)
        if self._active_id == session_id:
            self._active_id = None
        if delete_binding:
            self._closed_session_results[session_id] = result
            self._pending_last_choices.pop(session_id, None)
            self._session_state_generations.pop(session_id, None)
            self._current_root_turn_ids.pop(session_id, None)
            self._last_root_turn_states.pop(session_id, None)
            self._last_event_sequences.pop(session_id, None)
            for request_id, (_, create_task) in tuple(
                self._session_create_requests.items()
            ):
                if (
                    create_task.done()
                    and not create_task.cancelled()
                    and create_task.exception() is None
                    and create_task.result().session_id == session_id
                ):
                    self._session_create_requests.pop(request_id, None)
        await self._release_turn_idle_waiters(session_id)
        return result

    async def delete(self, session_id: str) -> bool:
        """删除 Trowel 中的指定会话并清理相关运行状态。

        Claude Code 或 Codex 保存的原生会话不会被删除。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            删除成功时返回 True；会话不存在时返回 False。
        """

        result = await self.close_result(session_id)
        if result.status == "not_found":
            return False
        if result.status == "needs_reconcile":
            raise SessionReconcileRequiredError(
                result.error or "session resources still need reconciliation"
            )
        return True

    async def close_all(self) -> dict[str, SessionCloseResult]:
        """并发收敛当前实例实时登记的会话，并保留全部 binding。

        Returns:
            以 Trowel 会话 ID 为键的关闭结果；断开连接的历史 binding 不在结果中。
        """

        self.begin_drain()
        live_session_ids = tuple(
            dict.fromkeys(
                session_id
                for runtime in self._runtime_ports.values()
                for session_id in runtime.session_ids()
                if self._store.get(session_id) is not None
            )
        )
        if not live_session_ids:
            return {}
        outcomes = await asyncio.gather(
            *(
                self.close_result(session_id, delete_binding=False)
                for session_id in live_session_ids
            ),
            return_exceptions=True,
        )
        results: dict[str, SessionCloseResult] = {}
        for session_id, outcome in zip(live_session_ids, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                results[session_id] = SessionCloseResult(
                    status="needs_reconcile",
                    remaining_resource_count=1,
                    remaining_resource_kinds=("session_close",),
                    error=f"session close failed: {type(outcome).__name__}",
                )
            else:
                results[session_id] = outcome
        return results

    async def stream(self, session_id: str, text: str) -> AsyncIterator[dict[str, Any]]:
        """原子取得委派在跑名额后，按 binding 产出统一事件。"""

        async for event in self._stream_turn(session_id, text, autonomous=False):
            yield event

    async def _stream_turn(
        self,
        session_id: str,
        text: str,
        *,
        autonomous: bool,
    ) -> AsyncIterator[dict[str, Any]]:
        """按普通输入或内部通知语义启动并消费一个完整 turn。

        Args:
            session_id: 接收输入的 Trowel 会话 ID。
            text: 交给父 runtime 的输入正文。
            autonomous: 是否由 Agent Host 内部事件触发；为 True 时不把输入伪装成
                前端已经乐观创建的用户 turn。

        Yields:
            Claude Code 或 Codex 转换后的统一事件。
        """

        self._require_accepting_work()
        binding = self._require(session_id)
        reservation = self._reserve_turn(binding)
        try:
            async for event in self._stream_admitted(
                binding, text, autonomous=autonomous
            ):
                yield event
        finally:
            self._capacity.release_turn(reservation)
            await self._notify_turn_state_changed(session_id)

    async def wait_until_idle(self, session_id: str) -> None:
        """等待指定会话没有未结束 turn，不占用模型或 MCP 工具调用。

        原生终态会主动唤醒等待者。Claude Code 客户端断开后的 drain 没有经过
        Session Hub 事件流，因此每秒重新核对一次实时状态作为恢复兜底。

        Args:
            session_id: 要等待的父 Trowel 会话 ID。

        Raises:
            SessionNotFoundError: 等待期间父会话被删除。
        """

        condition = self._turn_idle_conditions.setdefault(
            session_id, asyncio.Condition()
        )
        while True:
            binding = self._require(session_id)
            if not self._live_status(binding)[1]:
                return
            async with condition:
                binding = self._require(session_id)
                if not self._live_status(binding)[1]:
                    return
                try:
                    await asyncio.wait_for(condition.wait(), timeout=1.0)
                except TimeoutError:
                    pass

    def run_automatic_turn(
        self, session_id: str, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        """为 Agent Host 内部通知启动统一父会话 turn。

        Args:
            session_id: 接收通知的父 Trowel 会话 ID。
            text: 包含委派状态快照和处理要求的内部消息。

        Returns:
            与普通消息相同的统一事件迭代器；调用方必须持续消费到终态。
        """

        return self._stream_turn(session_id, text, autonomous=True)

    async def _notify_turn_state_changed(self, session_id: str) -> None:
        """唤醒等待指定会话重新核对空闲状态的内部任务。"""

        condition = self._turn_idle_conditions.get(session_id)
        if condition is None:
            return
        async with condition:
            condition.notify_all()

    async def _release_turn_idle_waiters(self, session_id: str) -> None:
        """关闭会话时唤醒等待者，并删除该会话的空闲条件。"""

        condition = self._turn_idle_conditions.pop(session_id, None)
        if condition is None:
            return
        async with condition:
            condition.notify_all()

    async def _stream_admitted(
        self,
        binding: SessionBinding,
        text: str,
        *,
        autonomous: bool,
        accepted_turn_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """产出已经通过全局在跑准入的会话事件。

        Args:
            binding: 已通过准入的会话记录。
            text: 要发送给会话的输入。
            autonomous: 是否由 Agent Host 内部通知启动本轮。
            accepted_turn_id: detached `/turns` 已返回给 renderer 的稳定根 turn ID；
                其他调用链为 None，不合成起点。

        Yields:
            Claude Code 或 Codex 转换后的统一事件。
        """

        session_id = binding.session_id
        if binding.runtime is Runtime.CLAUDE_CODE:
            host = self._cc_registry.get(session_id)
            if host is None:
                raise SessionNotFoundError(f"cc session {session_id} not live")
            cc_adapter = self._cc_adapters.get(session_id)
            if cc_adapter is None:
                cc_adapter = ClaudeCodeEventAdapter(session_id)
                self._cc_adapters[session_id] = cc_adapter
            observation_id = self._prepare_turn_observation(binding)
            root_started = False
            try:
                async for event in host.send(text):
                    raw = dict(event) if isinstance(event, dict) else event.model_dump()
                    if (
                        not root_started
                        and raw.get("type") != "turn_start"
                        and isinstance(accepted_turn_id, str)
                    ):
                        accepted = self._cc_accepted_turn_start(session_id, cc_adapter)
                        self._observe(accepted)
                        self._publish_agent_event(session_id, accepted)
                        root_started = True
                        yield accepted
                    if autonomous and raw.get("type") == "turn_start":
                        raw["autonomous"] = True
                    envelope = cc_adapter.wrap(raw).model_dump(by_alias=True)
                    root_started = root_started or raw.get("type") == "turn_start"
                    self._observe(envelope)
                    self._publish_agent_event(session_id, envelope)
                    if raw.get("type") == "session_started" or raw.get("type") in (
                        _TURN_TERMINAL_TYPES | {"session_exited"}
                    ):
                        # Client 可能在终态后立即关闭 SSE；原生身份必须先于 yield 落盘。
                        self._writeback_cc_native(session_id, host)
                    yield envelope
                self._writeback_cc_native(session_id, host)
            finally:
                self._abort_turn_observation(session_id, observation_id)
            return
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        _reject_reserved_codex_command(text)
        observation_id = self._prepare_turn_observation(binding)
        queue = self._add_codex_event_subscriber(session_id, session)
        turn_id: str | None = None
        try:
            send_options: dict[str, Any] = {
                "before_turn_start": lambda attached: self._writeback_codex_before_turn(
                    session_id, attached
                )
            }
            if autonomous:
                send_options.update(autonomous=True, memory_eligible=True)
            turn_id = await self._codex.send(session, text, **send_options)
            # turn 接受后再写回已提交的有效设置。
            self._writeback_codex_native(session_id, session)
        except TurnConflictError as exc:
            self._remove_codex_event_subscriber(session_id, queue)
            self._abort_turn_observation(session_id, observation_id)
            raise SessionConflictError(str(exc)) from exc
        except SessionHubError:
            self._remove_codex_event_subscriber(session_id, queue)
            self._abort_turn_observation(session_id, observation_id)
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为 502，不能落入 500。
            self._remove_codex_event_subscriber(session_id, queue)
            self._abort_turn_observation(session_id, observation_id)
            _log.warning("codex turn start failed for %s: %s", session_id, exc)
            raise RuntimeTurnError(f"codex turn failed: {exc}") from exc
        except BaseException:
            self._remove_codex_event_subscriber(session_id, queue)
            self._abort_turn_observation(session_id, observation_id)
            raise
        try:
            while True:
                payload = await queue.get()
                if payload is None:
                    break
                yield payload
                if _is_terminal(payload) and payload.get("turn_id") == turn_id:
                    break
        finally:
            self._remove_codex_event_subscriber(session_id, queue)
            self._abort_turn_observation(session_id, observation_id)

    async def start_codex_turn(
        self,
        session_id: str,
        text: str,
        *,
        autonomous: bool = False,
        memory_eligible: bool = True,
    ) -> str:
        """向指定 Codex 会话发送一条输入并启动新一轮处理。

        函数在 Codex 接受请求后返回，不等待这一轮结束；后续事件由
        subscribe_codex_events 返回。

        Args:
            session_id: 接收输入的 Codex 会话 ID。
            text: 要发送给 Codex 的文字内容。
            autonomous: 是否为应用内部启动，不合成顶层用户事件。
            memory_eligible: 本轮是否允许进入 Memory 提炼来源。

        Returns:
            Codex 为新一轮处理生成的轮次 ID。

        Raises:
            SessionNotFoundError: 找不到指定会话，或会话已不再运行。
            SessionOperationError: 会话由 Claude Code 运行，或输入是需要单独处理的
                Codex 斜杠命令。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
            SessionConflictError: Codex 当前已有一轮正在启动或运行。
            RuntimeTurnError: Codex 未能接受输入或保存会话信息。
        """

        self._require_accepting_work()
        binding = self._require(session_id)
        reservation = self._reserve_turn(binding)
        try:
            session = self._require_codex_session(session_id)
            codex = self._require_codex_runtime()
            _reject_reserved_codex_command(text)
            observation_id = self._prepare_turn_observation(binding)
            self._ensure_codex_event_pump(session_id, session)
            try:
                turn_id = await codex.send(
                    session,
                    text,
                    before_turn_start=lambda attached: (
                        self._writeback_codex_before_turn(session_id, attached)
                    ),
                    autonomous=autonomous,
                    memory_eligible=memory_eligible,
                )
                self._writeback_codex_native(session_id, session)
                return turn_id
            except TurnConflictError as exc:
                self._abort_turn_observation(session_id, observation_id)
                raise SessionConflictError(str(exc)) from exc
            except SessionHubError:
                self._abort_turn_observation(session_id, observation_id)
                raise
            except Exception as exc:  # noqa: BLE001
                self._abort_turn_observation(session_id, observation_id)
                _log.warning("codex turn start failed for %s: %s", session_id, exc)
                raise RuntimeTurnError(f"codex turn failed: {exc}") from exc
            except BaseException:
                self._abort_turn_observation(session_id, observation_id)
                raise
        finally:
            self._capacity.release_turn(reservation)

    async def start_turn(
        self,
        session_id: str,
        text: str,
        *,
        autonomous: bool = False,
        memory_eligible: bool = True,
        reserved_turn_id: str | None = None,
    ) -> str:
        """启动由应用级 SSE 承载结果的普通用户 turn。

        Codex 在原生 manager 接受输入后返回真实 turn ID。Claude Code 先预留与
        原生首帧共用的逻辑 turn ID，再由 Agent Host 后台任务持有输入消费，避免
        renderer 再建立一条 POST SSE。

        Args:
            session_id: 接收用户输入的 Trowel 会话 ID。
            text: 不会写入日志或错误的用户输入正文。
            autonomous: 是否为应用内部启动，不把文本当成顶层用户原话。
            memory_eligible: 本轮是否允许进入 Memory 提炼来源。
            reserved_turn_id: 内部 owner 为 Claude Code 预先持久化的逻辑 turn ID；
                Codex 仍使用原生返回的 ID。

        Returns:
            已被 runtime 接受的稳定根 turn ID。

        Raises:
            SessionConflictError: 同一 Claude Code 会话已有后台 turn，或后端容量拒绝。
            SessionHubError: 会话、runtime 或输入不满足既有启动契约。
        """

        binding = self._require(session_id)
        if binding.runtime is Runtime.CODEX:
            return await self.start_codex_turn(
                session_id,
                text,
                autonomous=autonomous,
                memory_eligible=memory_eligible,
            )
        existing = self._detached_turn_tasks.get(session_id)
        if existing is not None and not existing.done():
            raise SessionConflictError("session already has an in-flight turn")
        if self._live_status(binding)[1]:
            raise SessionConflictError("session already has an in-flight turn")
        reservation = self._reserve_turn(binding)
        host = self._cc_registry.get(session_id)
        if host is None:
            self._capacity.release_turn(reservation)
            raise SessionNotFoundError(f"cc session {session_id} not live")
        turn_id: str | None = None
        try:
            reserve_turn_id = getattr(host, "reserve_turn_id", None)
            if callable(reserve_turn_id):
                turn_id = reserve_turn_id(reserved_turn_id)
            else:
                turn_id = reserved_turn_id or uuid.uuid4().hex
            cc_adapter = self._cc_adapters.get(session_id)
            if cc_adapter is None:
                cc_adapter = ClaudeCodeEventAdapter(session_id)
                self._cc_adapters[session_id] = cc_adapter
            cc_adapter.begin_turn(turn_id)
            self._last_root_turn_states[session_id] = "starting"
            self._current_root_turn_ids[session_id] = turn_id
            self._touch_session_state(session_id)
            task = asyncio.create_task(
                self._consume_detached_turn(
                    binding,
                    text,
                    reservation,
                    autonomous=autonomous,
                    memory_eligible=memory_eligible,
                ),
                name=f"agent-detached-turn:{session_id}",
            )
        except BaseException:
            cancel_reserved_turn = getattr(host, "cancel_reserved_turn", None)
            if callable(cancel_reserved_turn) and turn_id is not None:
                cancel_reserved_turn(turn_id)
            self._capacity.release_turn(reservation)
            raise
        self._detached_turn_tasks[session_id] = task
        task.add_done_callback(
            lambda completed, key=session_id: self._discard_detached_turn_task(
                key, completed
            )
        )
        assert turn_id is not None
        return turn_id

    async def _consume_detached_turn(
        self,
        binding: SessionBinding,
        text: str,
        reservation: object,
        *,
        autonomous: bool,
        memory_eligible: bool,
    ) -> None:
        """消费 Claude Code turn 到终态，并把启动异常发布到应用级事件流。

        Args:
            binding: 后台 turn 所属的用户会话记录。
            text: 交给 Claude Code 的用户输入正文。
            reservation: `/turns` 返回前取得的用户在跑容量令牌。
            autonomous: 是否为应用内部启动。
            memory_eligible: 本轮是否允许进入 Memory 提炼来源；CC 目前按会话级
                门禁，参数仍保留为跨 runtime 的显式契约。
        """

        session_id = binding.session_id
        terminal_seen = False
        session_exited = False
        local_completion_seen = False
        root_started = False
        try:
            async for event in self._stream_admitted(
                binding,
                text,
                autonomous=autonomous,
                accepted_turn_id=self._current_root_turn_ids.get(session_id),
            ):
                root_started = root_started or event.get("type") == "turn_start"
                terminal_seen = terminal_seen or _is_terminal(event)
                session_exited = session_exited or event.get("type") == "session_exited"
                local_completion_seen = local_completion_seen or event.get("type") in {
                    "local_command",
                    "model_changed",
                }
            if terminal_seen or session_exited:
                return
            if local_completion_seen:
                adapter = self._cc_adapters[session_id]
                envelope = adapter.wrap(
                    {
                        "type": "finished",
                        "usage": {},
                        "total_cost_usd": None,
                        "num_turns": None,
                        "synthetic_reason": "local_command_completed",
                    }
                ).model_dump(by_alias=True)
            else:
                envelope = self.error_envelope(
                    session_id,
                    RuntimeTurnError(
                        "Claude Code stream closed without a terminal event"
                    ),
                )
            self._observe(envelope)
            self._publish_agent_event(session_id, envelope)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台入口必须把失败交回 renderer。
            _log.warning(
                "detached Claude Code turn failed: %s",
                type(exc).__name__,
            )
            if not root_started:
                adapter = self._cc_adapters[session_id]
                accepted = self._cc_accepted_turn_start(session_id, adapter)
                self._observe(accepted)
                self._publish_agent_event(session_id, accepted)
            envelope = self.error_envelope(session_id, exc)
            self._observe(envelope)
            self._publish_agent_event(session_id, envelope)
        finally:
            self._capacity.release_turn(reservation)
            await self._notify_turn_state_changed(session_id)

    def _cc_accepted_turn_start(
        self,
        session_id: str,
        adapter: ClaudeCodeEventAdapter,
    ) -> dict[str, Any]:
        """生成首个原生事件缺失时使用的稳定根 turn 起点。

        Args:
            session_id: 已经接受输入的 Claude Code 会话 ID。
            adapter: 预先绑定了稳定 turn ID 的会话事件适配器。

        Returns:
            可以直接发布的合成 turn_start 信封。
        """

        turn_id = self._current_root_turn_ids.get(session_id)
        if not isinstance(turn_id, str):
            raise RuntimeTurnError("Claude Code accepted turn has no stable ID")
        return adapter.wrap(
            {
                "type": "turn_start",
                "turn_id": turn_id,
                "autonomous": False,
                "revertible": False,
                "synthetic_reason": "agent_host_accepted",
            }
        ).model_dump(by_alias=True)

    def _discard_detached_turn_task(
        self, session_id: str, completed: asyncio.Task[None]
    ) -> None:
        """移除已结束的 Claude Code 后台消费任务并取走异常。

        Args:
            session_id: 任务所属的 Trowel 会话 ID。
            completed: 已触发 done callback 的任务对象。
        """

        if self._detached_turn_tasks.get(session_id) is completed:
            self._detached_turn_tasks.pop(session_id, None)
        if not completed.cancelled():
            completed.exception()

    def subscribe_codex_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """持续返回指定 Codex 会话产生的事件。

        此订阅不会启动新一轮处理，也不会在某一轮结束时自动结束。多个订阅者会各自
        收到相同的事件。

        Args:
            session_id: 要订阅的 Codex 会话 ID。

        Yields:
            Codex 产生的会话事件，包括回复文本、工具调用和状态变化等。

        Raises:
            SessionNotFoundError: 找不到指定会话，或会话已不再运行。
            SessionOperationError: 指定会话由 Claude Code 运行。
            RuntimeUnavailableError: 未配置 Codex 会话管理器。
        """

        async def iterate() -> AsyncIterator[dict[str, Any]]:
            """逐个返回收到的事件，并在停止迭代时取消本次订阅。"""

            session = self._require_codex_session(session_id)
            queue = self._add_codex_event_subscriber(session_id, session)
            try:
                while True:
                    payload = await queue.get()
                    if payload is None:
                        return
                    yield payload
            finally:
                self._remove_codex_event_subscriber(session_id, queue)

        return iterate()

    def require_event_session(self, session_id: str) -> SessionBinding:
        """确认会话仍有可供常驻订阅接收事件的 runtime 对象。

        Claude Code 进程按 turn 懒启动并在轮间退出，因此可订阅性取决于 Host 是否仍
        持有会话对象，不能使用当前子进程的 connected 状态判断。

        Args:
            session_id: 要建立实时订阅的 Trowel 会话 ID。

        Returns:
            经过持久 binding 与 runtime 对象双重确认的会话记录。

        Raises:
            SessionNotFoundError: binding 不存在或对应 runtime 对象已经删除。
        """

        binding = self._require(session_id)
        if binding.runtime is Runtime.CLAUDE_CODE:
            if session_id not in self._cc_registry:
                raise SessionNotFoundError(f"cc session {session_id} not live")
        else:
            self._require_codex_session(session_id)
        return binding

    def subscribe_agent_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """持续返回 Claude Code 或 Codex 会话之后产生的统一事件。

        该订阅不重放历史。Claude Code 的消息请求和 Agent Host 内部续轮会把同一
        AgentEvent fan-out 到这里；Codex 继续由唯一原生 reader 负责 fan-out。

        Args:
            session_id: 要订阅的 Trowel 会话 ID。

        Yields:
            建立订阅后产生的统一 AgentEvent。
        """

        async def iterate() -> AsyncIterator[dict[str, Any]]:
            """登记一个公共订阅者，并在停止迭代时移除自己的队列。"""

            binding = self.require_event_session(session_id)
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            self._agent_event_subscribers.setdefault(session_id, set()).add(queue)
            if binding.runtime is Runtime.CODEX:
                session = self._require_codex_session(session_id)
                self._ensure_codex_event_pump(session_id, session)
            try:
                while True:
                    payload = await queue.get()
                    if payload is None:
                        return
                    yield payload
            finally:
                subscribers = self._agent_event_subscribers.get(session_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._agent_event_subscribers.pop(session_id, None)

        return iterate()

    def subscribe_application_events(
        self, *, queue_capacity: int = 512
    ) -> ApplicationEventSubscription:
        """订阅当前应用全部 user session 的统一 AgentEvent。

        新建和恢复的用户会话会自动进入同一个订阅。delegate 与 probe 会话保留原有
        owner 边界，不进入 renderer 的公开应用流。

        Args:
            queue_capacity: 单个 renderer 最多积压的普通事件数。溢出时只报告受影响
                session 的缺口，不关闭其他 session 的事件流。

        Returns:
            可接收事件、局部缺口和 heartbeat 超时的应用订阅对象。
        """

        return self._application_events.subscribe(queue_capacity)

    def _publish_agent_event(self, session_id: str, payload: dict[str, Any]) -> None:
        """把一条统一事件复制给当前会话的全部常驻订阅者。

        Args:
            session_id: 事件所属的 Trowel 会话 ID。
            payload: 已转换为 wire 字典的 AgentEvent。
        """

        self._observe_live_event(session_id, payload)
        binding = self._store.get(session_id)
        if binding is not None and binding.session_kind == "user":
            self._application_events.publish(payload)
        for queue in tuple(self._agent_event_subscribers.get(session_id, ())):
            queue.put_nowait(payload)

    def _observe_live_event(self, session_id: str, payload: Mapping[str, Any]) -> None:
        """把已发布事件折叠为 session snapshot 使用的根 turn 事实。

        Args:
            session_id: 事件所属的 Trowel 会话 ID。
            payload: 已转换的 AgentEvent wire 字典。
        """

        sequence = payload.get("seq")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            self._last_event_sequences[session_id] = sequence
        binding = self._store.get(session_id)
        if binding is None:
            return
        thread_id = payload.get("thread_id")
        root_event = binding.runtime is Runtime.CLAUDE_CODE or (
            binding.native_session_id is not None
            and thread_id == binding.native_session_id
        )
        if root_event:
            event_type = payload.get("type")
            turn_id = payload.get("turn_id")
            if event_type == "turn_start":
                self._current_root_turn_ids[session_id] = (
                    turn_id if isinstance(turn_id, str) else None
                )
                self._last_root_turn_states[session_id] = "running"
            elif event_type in _TURN_TERMINAL_TYPES:
                current_turn_id = self._current_root_turn_ids.get(session_id)
                identity_matches = (
                    isinstance(current_turn_id, str)
                    and isinstance(turn_id, str)
                    and turn_id == current_turn_id
                ) or (
                    binding.runtime is Runtime.CLAUDE_CODE
                    and current_turn_id is None
                    and turn_id is None
                    and self._last_root_turn_states.get(session_id) == "starting"
                )
                if identity_matches:
                    terminal_states = {
                        "finished": "completed",
                        "interrupted": "interrupted",
                        "error": "failed",
                    }
                    self._last_root_turn_states[session_id] = terminal_states[
                        str(event_type)
                    ]
                    self._current_root_turn_ids[session_id] = (
                        turn_id if isinstance(turn_id, str) else current_turn_id
                    )
        self._touch_session_state(session_id)

    def _add_codex_event_subscriber(
        self, session_id: str, session: Any
    ) -> asyncio.Queue[dict[str, Any] | None]:
        """为一个订阅者创建事件队列，并确保该会话的事件读取任务正在运行。

        Args:
            session_id: 要订阅的 Codex 会话 ID。
            session: 已登记的 Codex 会话对象。

        Returns:
            供该订阅者读取的事件队列；队列中的 None 表示事件读取已经结束。
        """

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._codex_event_subscribers.setdefault(session_id, set()).add(queue)
        self._ensure_codex_event_pump(session_id, session)
        return queue

    def _ensure_codex_event_pump(self, session_id: str, session: Any) -> None:
        """保证 Codex 原生事件始终进入内部观察器，不依赖前端订阅。

        Args:
            session_id: 事件所属的 Trowel 会话 ID。
            session: 提供唯一原生事件 reader 的 Codex 会话。
        """

        task = self._codex_event_tasks.get(session_id)
        if task is None or task.done():
            self._codex_event_tasks[session_id] = asyncio.create_task(
                self._pump_codex_events(session_id, session),
                name=f"codex-events-{session_id}",
            )

    def _remove_codex_event_subscriber(
        self, session_id: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        """停止向一个调用方发送会话事件，但不中断 Codex 当前的处理。

        Args:
            session_id: Codex 会话 ID。
            queue: 该调用方接收事件的队列。
        """

        subscribers = self._codex_event_subscribers.get(session_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._codex_event_subscribers.pop(session_id, None)

    async def _pump_codex_events(self, session_id: str, session: Any) -> None:
        """持续读取 Codex 会话产生的事件，转换后交给内部模块和所有接收方。

        即使当前没有接收方，也会继续读取事件。读取任务结束时，会通知仍在等待的
        调用方停止等待。

        Args:
            session_id: Codex 会话 ID。
            session: 要从中读取事件的 Codex 会话对象。
        """

        adapter = self._codex_adapters.get(session_id)
        if adapter is None:
            adapter = CodexEventAdapter(session_id)
            self._codex_adapters[session_id] = adapter
        try:
            async for event in session.events():
                envelope = adapter.wrap(event)
                if envelope is None:
                    continue
                payload = envelope.model_dump(by_alias=True)
                self._observe(payload)
                self._publish_agent_event(session_id, payload)
                for queue in tuple(self._codex_event_subscribers.get(session_id, ())):
                    queue.put_nowait(payload)
                if _is_terminal(payload):
                    await self._notify_turn_state_changed(session_id)
        except asyncio.CancelledError:
            raise
        finally:
            for queue in tuple(self._codex_event_subscribers.get(session_id, ())):
                queue.put_nowait(None)

    def _stop_codex_event_pump(self, session_id: str) -> None:
        """停止读取指定 Codex 会话的事件，并让所有等待事件的调用方退出等待。

        此操作只停止事件传递，不会中断 Codex 正在执行的任务。

        Args:
            session_id: Codex 会话 ID。
        """

        task = self._codex_event_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
        for queue in tuple(self._codex_event_subscribers.pop(session_id, ())):
            queue.put_nowait(None)

    def _stop_session_event_delivery(self, session_id: str) -> None:
        """停止会话的后台 turn、原生 reader，并关闭旧的单会话订阅。

        Args:
            session_id: 正在关闭的 Trowel 会话 ID。
        """

        detached = self._detached_turn_tasks.pop(session_id, None)
        if detached is not None and not detached.done():
            detached.cancel()
        self._stop_codex_event_pump(session_id)
        for queue in tuple(self._agent_event_subscribers.pop(session_id, ())):
            queue.put_nowait(None)

    def _observe(self, payload: Mapping[str, Any]) -> None:
        """把一个会话事件交给创建 SessionHub 时传入的处理函数。

        没有配置处理函数时直接返回；处理失败只记录日志，不影响当前会话继续运行。

        Args:
            payload: 已转换为统一格式的会话事件。
        """

        if self._event_observer is None:
            return
        try:
            self._event_observer(payload)
        except Exception:
            _log.warning("[hub] event observer raised; ignored", exc_info=True)

    def _prepare_turn_observation(self, binding: SessionBinding) -> str | None:
        """在 runtime 请求前保存当前 HTTP 关联，观察失败不影响会话。

        Args:
            binding: 提供 Trowel 会话 ID 和已冻结 runtime 的会话记录。
        """

        if self._turn_observer is None:
            return None
        runtime: Literal["claude_code", "codex"] = (
            "claude_code" if binding.runtime is Runtime.CLAUDE_CODE else "codex"
        )
        try:
            return self._turn_observer.prepare_turn(binding.session_id, runtime)
        except Exception:
            _log.warning("[hub] turn observer prepare raised; ignored", exc_info=True)
            return None

    def _abort_turn_observation(
        self,
        session_id: str,
        observation_id: str | None,
    ) -> None:
        """收口没有正常 terminal 的观察状态，失败不影响 runtime。

        Args:
            session_id: 当前 runtime 请求所属的 Trowel 会话 ID。
            observation_id: prepare 返回的观察代次；观察未启动时为 None。
        """

        if self._turn_observer is None or observation_id is None:
            return
        try:
            self._turn_observer.abort_turn(session_id, observation_id)
        except Exception:
            _log.warning("[hub] turn observer abort raised; ignored", exc_info=True)

    def error_envelope(self, session_id: str, detail: Any) -> dict[str, Any]:
        """把错误信息转换成可通过事件流发送的结束事件。

        找得到会话时，事件编号会接在该会话的前一个事件之后；找不到会话记录时，
        仍会生成一个最基本的错误事件。

        Args:
            session_id: 错误所属的会话 ID。
            detail: 异常对象或错误说明，写入事件时会转换成字符串。

        Returns:
            可直接发送给客户端的错误事件。
        """

        binding = self._store.get(session_id)
        if binding is None:
            return AgentEvent(
                session_id=session_id,
                runtime="claude_code",
                seq=1,
                type="error",
                payload={"subclass": "host_error", "errors": [str(detail)]},
            ).model_dump(by_alias=True)
        if binding.runtime is Runtime.CLAUDE_CODE:
            cc_adapter = self._cc_adapters.get(session_id)
            if cc_adapter is None:
                cc_adapter = ClaudeCodeEventAdapter(session_id)
                self._cc_adapters[session_id] = cc_adapter
            return cc_adapter.error_event(detail).model_dump(by_alias=True)
        codex_adapter = self._codex_adapters.get(session_id)
        if codex_adapter is None:
            codex_adapter = CodexEventAdapter(session_id)
            self._codex_adapters[session_id] = codex_adapter
        return codex_adapter.error_event(detail).model_dump(by_alias=True)

    def _writeback_cc_native(self, session_id: str, host: Any) -> None:
        """把 Claude Code 当前的会话 ID、模型、思考强度和权限保存到会话记录。

        只保存已经取得的信息，不会用 None 清空原记录；保存前会话已被删除时直接忽略。

        Args:
            session_id: 要更新的会话 ID。
            host: 提供当前运行信息的 Claude Code 会话对象。
        """

        cc_session_id = getattr(host, "cc_session_id", None)
        model = getattr(host, "effective_model", None) or getattr(host, "model", None)
        if cc_session_id is None and model is None:
            return
        binding = self._store.get(session_id)
        if binding is None:
            _log.debug("cc writeback skipped, binding %s gone", session_id)
            return
        self._lifecycle.remember_non_user_identity(binding, cc_session_id)
        try:
            candidate_changes = {
                "native_session_id": cc_session_id,
                "model": model,
                "effort": getattr(host, "effort", None),
                "permission": getattr(host, "permission_mode", None),
            }
            candidate = replace(
                binding,
                **{
                    key: value
                    for key, value in candidate_changes.items()
                    if value is not None
                },
            )
            # 先落脱敏恢复条件；档案失败时不能让 binding 进入不可安全恢复的半状态。
            self._configuration_archive.put(candidate)
            updated = self._store.update_native(
                session_id,
                native_session_id=cc_session_id,
                model=model,
                effort=getattr(host, "effort", None),
                permission=getattr(host, "permission_mode", None),
            )
            self._persist_native_title(updated)
            if updated.native_session_id:
                self._record_last_choice_once(session_id)
        except KeyError:
            _log.debug("cc writeback skipped, binding %s gone", session_id)

    def _writeback_codex_native(self, session_id: str, session: Any) -> None:
        """把 Codex 当前的线程 ID、模型、思考强度、权限和网络设置保存到会话记录。

        Codex 尚未提供模型时不保存；缺少的值不会清空原记录。保存前会话已被删除时
        直接忽略。

        Args:
            session_id: 要更新的会话 ID。
            session: 提供当前线程和运行设置的 Codex 会话对象。
        """

        thread_binding = getattr(session, "binding", None)
        if thread_binding is None or not thread_binding.model:
            return
        binding = self._store.get(session_id)
        if binding is None:
            _log.debug("codex writeback skipped, binding %s gone", session_id)
            return
        self._lifecycle.remember_non_user_identity(binding, thread_binding.thread_id)
        try:
            sandbox = getattr(thread_binding, "effective_sandbox", None)
            approval = getattr(thread_binding, "effective_approval", None)
            candidate_changes = {
                "native_session_id": thread_binding.thread_id,
                "model": thread_binding.model,
                "effort": getattr(thread_binding, "reasoning_effort", None),
                "permission": _permission_label(sandbox, approval),
                "effective_permission_profile": getattr(
                    thread_binding, "permission_profile", None
                ),
                "effective_sandbox": sandbox,
                "effective_approval": approval,
                "network_access": getattr(thread_binding, "network_access", None),
            }
            candidate = replace(
                binding,
                **{
                    key: value
                    for key, value in candidate_changes.items()
                    if value is not None
                },
            )
            self._configuration_archive.put(candidate)
            updated = self._store.update_native(
                session_id,
                native_session_id=thread_binding.thread_id,
                model=thread_binding.model,
                effort=getattr(thread_binding, "reasoning_effort", None),
                permission=_permission_label(sandbox, approval),
                effective_permission_profile=getattr(
                    thread_binding, "permission_profile", None
                ),
                effective_sandbox=sandbox,
                effective_approval=approval,
                network_access=getattr(thread_binding, "network_access", None),
            )
            self._persist_native_title(updated)
            if updated.native_session_id:
                self._record_last_choice_once(session_id)
        except KeyError:
            _log.debug("codex writeback skipped, binding %s gone", session_id)

    def _record_last_choice_once(self, session_id: str) -> None:
        """原生会话和 binding 都存在后，尽力写回连接最近成功选择。"""

        launch = self._pending_last_choices.get(session_id)
        recorder = self._last_choice_recorder
        if launch is None or recorder is None:
            return
        binding = self._store.get(session_id)
        if binding is None or binding.session_kind != "user":
            self._pending_last_choices.pop(session_id, None)
            return
        try:
            recorder(launch)
            self._pending_last_choices.pop(session_id, None)
        except Exception:  # noqa: BLE001 - 最近选择不能反向破坏已创建的会话。
            _log.warning(
                "failed to record last session choice for connection %s",
                launch.connection_id,
                exc_info=True,
            )

    def _writeback_codex_before_turn(self, session_id: str, session: Any) -> None:
        """在 Codex 开始新一轮处理前保存线程信息，并确认线程 ID 已正确写入会话记录。

        保存或检查失败时会阻止本轮启动，避免 Codex 已经开始工作，但 Trowel 没有记住
        对应的线程 ID。

        Args:
            session_id: 要更新的会话 ID。
            session: 包含当前线程信息的 Codex 会话对象。

        Raises:
            KeyError: 线程 ID 无效、会话记录已被删除，或保存后的线程 ID 不一致。
        """

        self._writeback_codex_native(session_id, session)
        persisted = self._store.get(session_id)
        thread_binding = getattr(session, "binding", None)
        thread_id = getattr(thread_binding, "thread_id", None)
        if (
            not isinstance(thread_id, str)
            or not thread_id
            or persisted is None
            or persisted.native_session_id != thread_id
        ):
            raise KeyError(session_id)


def _native_turn_ids(thread: Mapping[str, Any]) -> set[str]:
    """收集 Codex 线程记录中所有字符串形式的轮次 ID。

    Args:
        thread: Codex 返回的线程记录。

    Returns:
        找到的轮次 ID 集合；turns 字段不存在或不是列表时返回空集合。
    """

    turns = thread.get("turns")
    if not isinstance(turns, list):
        return set()
    return {
        turn_id
        for turn in turns
        if isinstance(turn, Mapping) and isinstance((turn_id := turn.get("id")), str)
    }


def _is_terminal(payload: dict[str, Any]) -> bool:
    """判断一条会话事件是否表示 Codex 当前的处理已经结束。

    处理完成、中断、最终失败或 Codex 进程退出时返回 True；仍在重试的错误返回
    False。此函数不检查事件属于哪一轮，调用方还需核对轮次 ID。

    Args:
        payload: 已转换为统一格式的会话事件。

    Returns:
        该事件表示处理已经结束时返回 True，否则返回 False。
    """

    event_type = payload.get("type")
    if event_type in _TURN_TERMINAL_TYPES:
        return True
    if event_type == "host_status":
        nested = payload.get("payload")
        if isinstance(nested, dict) and nested.get("status") == "host_exited":
            return True
    return False


def _permission_label(sandbox: str | None, approval: str | None) -> str | None:
    """把 Codex 的文件操作范围和确认策略合并成 permission 字段的显示文字。

    Args:
        sandbox: Codex 可以修改文件的范围；None 表示尚未取得。
        approval: 哪些操作需要用户确认；None 表示尚未取得。

    Returns:
        合并后的权限说明；两项都为 None 时返回 None。
    """

    labels = {
        "read-only": "Read only",
        "workspace-write": "Workspace write",
        "danger-full-access": "Full access",
    }
    if sandbox is None and approval is None:
        return None
    sandbox_label = labels.get(sandbox or "", sandbox or "Unknown sandbox")
    return f"{sandbox_label} · {approval or 'unknown approval'}"
