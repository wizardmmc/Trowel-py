"""协调 CC 与 Codex 会话的绑定、路由和生命周期。

持久化 binding 是会话创建后唯一的 runtime 路由依据；模型和界面状态都不能代替它。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

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
from trowel_py.agent_host.delegate_identity import (
    DelegateIdentityStore,
    delegate_identity_path,
)
from trowel_py.agent_host.lifecycle import SessionInFlightError, SessionLifecycle
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
from trowel_py.codex_host.pending_requests import (
    PendingRequestConflictError,
    PendingRequestDecisionError,
    PendingRequestNotFoundError,
    PendingRequestOwnershipError,
)
from trowel_py.codex_host.commands import reserved_command_name
from trowel_py.codex_host.session import TurnConflictError
from trowel_py.cc_host.session_lifecycle import (
    CcCapacityError,
    CcWorkdirNotFoundError,
)
from trowel_py.agent_host.store import BindingStore, next_session_display_name
from trowel_py.agent_host.title_store import (
    SessionTitleRecord,
    SessionTitleStore,
    resolve_title_store_path,
)
from trowel_py.agent_host.events import AgentEvent

_log = logging.getLogger(__name__)

# capability 是界面的能力发现契约，界面不能从 runtime 推断功能。
CC_CAPABILITIES: tuple[str, ...] = ("tools", "approval", "checkpoint", "workflow")
CODEX_CAPABILITIES: tuple[str, ...] = ("tools", "approval", "subagents")

# 连接上限按仍有 binding 的已注册 session/thread 计数，共享 manager 不合并名额。
MAX_CONNECTIONS = USER_CONNECTION_LIMIT
# 用户会话的在跑上限仍由前端执行；该常量保留公开兼容。
MAX_RUNNING = USER_RUNNING_LIMIT
# 委派子会话由后端单独限制，两个 runtime 共用同一组连接和在跑名额。
MAX_DELEGATE_CONNECTIONS = DELEGATE_CONNECTION_LIMIT
MAX_DELEGATE_RUNNING = DELEGATE_RUNNING_LIMIT

_TURN_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error"})


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
        delegate_identity_store: DelegateIdentityStore | None = None,
        runtime_ports: Mapping[Runtime, RuntimeSessionPort] | None = None,
        capacity_limits: CapacityLimits | None = None,
        session_review_requester: SessionReviewRequester | None = None,
        title_generator: SessionTitleGenerator | None = None,
        title_store: SessionTitleStore | None = None,
        codex_history_root: str | Path | None = None,
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
            delegate_identity_store: 跨 binding 清理保留委派原生会话 ID 的本机索引；
                未提供时在 binding 文件旁创建独立索引。
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
        """

        self._store = store
        self._title_store = title_store or SessionTitleStore(
            resolve_title_store_path(store.path)
        )
        self._title_generator = title_generator
        self._title_lock = threading.Lock()
        self._delegate_identities = delegate_identity_store or DelegateIdentityStore(
            delegate_identity_path(store.path)
        )
        self._codex = codex_manager
        self._cc_registry = (
            cc_registry if cc_registry is not None else _default_cc_registry()
        )
        self._cc_opener = cc_opener if cc_opener is not None else _default_cc_opener()
        self._cc_proxy_base_url = cc_proxy_base_url
        self._cc_settings_path = cc_settings_path
        self._codex_config_home = (
            Path(codex_config_home) if codex_config_home is not None else None
        )
        self._event_observer = event_observer
        self._session_review_requester = session_review_requester
        self._codex_history_root = (
            Path(codex_history_root) if codex_history_root is not None else None
        )
        self._active_id: str | None = None
        # adapter 跨 turn 复用；被 adapter 丢弃的原生事件不占统一序号。
        self._cc_adapters: dict[str, ClaudeCodeEventAdapter] = {}
        self._codex_adapters: dict[str, CodexEventAdapter] = {}
        self._codex_event_subscribers: dict[
            str, set[asyncio.Queue[dict[str, Any] | None]]
        ] = {}
        self._codex_event_tasks: dict[str, asyncio.Task[None]] = {}
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
            self._delegate_identities,
            self._runtime_ports,
            self._capacity,
        )
        self._lifecycle.migrate_delegate_identities()

    @property
    def store(self) -> BindingStore:
        """获取保存了 Trowel 会话记录的存储对象。"""

        return self._store

    @property
    def codex_available(self) -> bool:
        """是否已经配置 Codex 进程和 thread 管理器。"""

        return self._codex is not None

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

    def create(self, req: CreateAgentSessionRequest) -> SessionBinding:
        """创建 Claude Code 或 Codex 会话，并保存对应的 Trowel 会话记录。

        此方法只完成会话登记和配置保存，不发送消息或启动轮次。

        Args:
            req: 运行工具、工作目录、模型、权限和上下文开关等创建配置。

        Returns:
            新创建的 Trowel 会话记录。

        Raises:
            InvalidSessionRequestError: 工作目录不存在或创建参数无效。
            SessionConflictError: 连接数已满，或 Codex 配置中存在同名 MCP。
            RuntimeUnavailableError: 请求使用 Codex，但未配置 Codex 会话管理器。
        """

        req = self._inherit_resume_config(req)
        if not Path(req.workdir).is_dir():
            raise InvalidSessionRequestError("workdir does not exist")
        try:
            with self._capacity.admit_connection(req.session_kind):
                if req.runtime == "claude_code":
                    return self._create_cc(req)
                return self._create_codex(req)
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
        previous = self._latest_binding(
            runtime=Runtime(req.runtime), native_session_id=req.resume_from
        )
        if previous is None:
            return req
        explicit = req.model_fields_set
        updates: dict[str, Any] = {}
        if req.runtime == "claude_code":
            for field in ("model", "effort"):
                if field not in explicit:
                    updates[field] = getattr(previous, field)
            if "permission_mode" not in explicit:
                updates["permission_mode"] = previous.permission
        elif "permission_preset" not in explicit:
            updates["permission_preset"] = previous.permission_preset
        for request_field, binding_field in (
            ("memory_enabled", "memory_enabled"),
            ("profile_enabled", "profile_enabled"),
            ("self_enabled", "self_enabled"),
        ):
            if request_field not in explicit:
                updates[request_field] = getattr(previous, binding_field)
        return req.model_copy(update=updates)

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

    def latest_session_defaults(self) -> dict[str, Any] | None:
        """读取最近创建或使用的会话配置，作为新建会话的默认值。

        Returns:
            包含运行工具、模型、思考强度、权限、Memory 和 Profile 开关的配置。
            Claude Code 使用 permission_mode；Codex 使用 permission_preset。
            没有历史会话记录时返回 None。
        """

        bindings = self._store.list_all()
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
        if binding.runtime is Runtime.CODEX and binding.permission_preset is not None:
            defaults["permission_preset"] = binding.permission_preset
        return defaults

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
            await self._codex.attach(session)
            self._writeback_codex_native(session_id, session)
        except TurnConflictError as exc:
            raise SessionConflictError(str(exc)) from exc
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为可诊断的 runtime 错误。
            raise RuntimeTurnError(f"codex resume failed: {exc}") from exc
        return self._require(session_id)

    def _create_cc(self, req: CreateAgentSessionRequest) -> SessionBinding:
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
        try:
            opened = self._cc_opener(
                cc_req,
                self._cc_registry,
                proxy_base_url=self._cc_proxy_base_url,
                settings_path=self._cc_settings_path,
                display_name=display_name,
            )
        except CcWorkdirNotFoundError as exc:
            raise InvalidSessionRequestError(str(exc)) from exc
        except CcCapacityError as exc:
            raise SessionConflictError(str(exc)) from exc
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
                profile_enabled=req.profile_enabled,
                self_enabled=req.self_enabled,
                session_kind=req.session_kind,
                memory_eligibility=req.memory_eligibility,
                agent_mcp_enabled=req.agent_mcp_enabled,
                parent_session_id=req.parent_session_id,
                delegation_depth=req.delegation_depth,
                capabilities=CC_CAPABILITIES,
                name=opened.name,
                display_title=display_title,
                title_source=title_source,
            )
        except BaseException as exc:
            self._lifecycle.abort_created(Runtime.CLAUDE_CODE, opened.sid, exc)
            raise
        self._lifecycle.commit_created(binding)
        self._persist_native_title(binding)
        if req.session_kind == "user":
            self._active_id = opened.sid
        return binding

    def _create_codex(self, req: CreateAgentSessionRequest) -> SessionBinding:
        """``resume_from`` 只登记原生 thread，首次 turn 才执行恢复。"""

        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        self._refuse_on_trowel_mcp_collision(req.workdir)
        prepared = prepare_codex_session(
            req,
            session_id_factory=lambda: uuid.uuid4().hex,
            permission_presets=_CODEX_PERMISSION_PRESETS,
            fingerprint=_injection_fingerprint,
        )
        sid = prepared.session_id
        display_title, title_source = self._initial_title(req)
        if self._codex is not None:
            self._codex.register(prepared.session)
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
                profile_enabled=req.profile_enabled,
                self_enabled=req.self_enabled,
                session_kind=req.session_kind,
                memory_eligibility=req.memory_eligibility,
                agent_mcp_enabled=req.agent_mcp_enabled,
                parent_session_id=req.parent_session_id,
                delegation_depth=req.delegation_depth,
                capabilities=CODEX_CAPABILITIES,
                name=self._display_name(req.workdir),
                permission_preset=prepared.permission_preset,
                injection_hash=prepared.injection_hash,
                declared_mcp_roster=prepared.declared_mcp_roster,
                display_title=display_title,
                title_source=title_source,
            )
        except BaseException as exc:
            self._lifecycle.abort_created(Runtime.CODEX, sid, exc)
            raise
        self._lifecycle.commit_created(binding)
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
        cc_delegate_ids = self._delegate_identities.ids(Runtime.CLAUDE_CODE)
        codex_delegate_ids = self._delegate_identities.ids(Runtime.CODEX)
        cc_summaries = await asyncio.to_thread(
            scan_cc_history,
            workdir,
            limit=required,
            excluded_ids=cc_delegate_ids,
        )
        codex_threads: list[dict[str, Any]] = []
        if self._codex is not None:
            codex_threads = await self._codex.list_threads(
                cwd=workdir,
                limit=required,
                excluded_ids=codex_delegate_ids,
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
            items.append(item)
        user_ids = {str(item["session_id"]) for item in items}
        active_id = self._active_id if self._active_id in user_ids else None
        return items, active_id

    def _live_status(self, binding: SessionBinding) -> tuple[bool, bool]:
        """计算会话列表中的 connected 和 running 状态。

        Claude Code 的 connected 表示子进程存在且尚未退出；Codex 的 connected 表示
        会话仍登记在 Codex 管理器中。running 表示当前有轮次正在执行。

        Args:
            binding: 要检查的 Trowel 会话记录。

        Returns:
            connected 和 running 组成的二元组，顺序为 connected、running。
        """

        state = self._capacity.live_state(binding)
        return state.connected, state.has_in_flight_turn

    def _reserve_delegate_turn(self, binding: SessionBinding) -> object | None:
        """预留委派在跑名额，并把容量拒绝转换为 Hub 冲突错误。"""

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
        reservation = self._reserve_delegate_turn(binding)
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
        reservation = self._reserve_delegate_turn(binding)
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

        await self._require_codex_runtime().attach(session)
        self._writeback_codex_before_turn(session_id, session)

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
        for binding in self._store.list_all():
            if binding.native_session_id != native_session_id:
                continue
            if binding.runtime is not runtime:
                raise CrossRuntimeResumeError(
                    f"native session {native_session_id!r} is bound to "
                    f"{binding.runtime.value}; cannot resume as "
                    f"{runtime.value} (C-2)"
                )
            if memory_enabled is not None and binding.memory_enabled != memory_enabled:
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"memory_enabled={binding.memory_enabled}; cannot resume "
                    f"as memory_enabled={memory_enabled} (C-2)"
                )
            if (
                profile_enabled is not None
                and binding.profile_enabled != profile_enabled
            ):
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"profile_enabled={binding.profile_enabled}; cannot "
                    f"resume as profile_enabled={profile_enabled} (C-2)"
                )
            if self_enabled is not None and binding.self_enabled != self_enabled:
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"self_enabled={binding.self_enabled}; cannot resume as "
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

    async def delete(self, session_id: str) -> bool:
        """删除 Trowel 中的指定会话并清理相关运行状态。

        Claude Code 或 Codex 保存的原生会话不会被删除。

        Args:
            session_id: Trowel 会话 ID。

        Returns:
            删除成功时返回 True；会话不存在时返回 False。
        """

        binding = self._store.get(session_id)
        if binding is None:
            return False
        if binding.runtime not in self._runtime_ports:
            raise RuntimeUnavailableError(f"{binding.runtime.value} host unavailable")
        try:
            review_requester = None
            if (
                binding.session_kind == "user"
                and binding.memory_enabled
                and self._session_review_requester is not None
            ):

                def persist_review_request() -> None:
                    """在 binding 删除前持久登记当前用户会话的 Memory review。"""

                    if self._session_review_requester is not None:
                        self._session_review_requester(binding)

                review_requester = persist_review_request
            await self._lifecycle.close(
                binding,
                require_idle=(
                    binding.session_kind == "delegate"
                    and binding.runtime is Runtime.CODEX
                ),
                busy_message="Codex 委派子会话仍在处理，尚不能确认清理完成",
                before_runtime_close=lambda: self._stop_codex_event_pump(session_id),
                before_binding_delete=review_requester,
            )
        except (CapacityConflictError, SessionInFlightError) as exc:
            raise SessionConflictError(str(exc)) from exc
        # 删除 adapter，避免复用 id 继承旧序号。
        self._cc_adapters.pop(session_id, None)
        self._codex_adapters.pop(session_id, None)
        if self._active_id == session_id:
            self._active_id = None
        return True

    async def stream(self, session_id: str, text: str) -> AsyncIterator[dict[str, Any]]:
        """原子取得委派在跑名额后，按 binding 产出统一事件。"""

        binding = self._require(session_id)
        reservation = self._reserve_delegate_turn(binding)
        try:
            async for event in self._stream_admitted(binding, text):
                yield event
        finally:
            self._capacity.release_turn(reservation)

    async def _stream_admitted(
        self,
        binding: SessionBinding,
        text: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """产出已经通过全局在跑准入的会话事件。

        Args:
            binding: 已通过准入的会话记录。
            text: 要发送给会话的输入。

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
            async for event in host.send(text):
                raw = dict(event) if isinstance(event, dict) else event.model_dump()
                envelope = cc_adapter.wrap(raw).model_dump(by_alias=True)
                self._observe(envelope)
                if raw.get("type") == "session_started" or raw.get("type") in (
                    _TURN_TERMINAL_TYPES | {"session_exited"}
                ):
                    # Client 可能在终态后立即关闭 SSE；原生身份必须先于 yield 落盘。
                    self._writeback_cc_native(session_id, host)
                yield envelope
            self._writeback_cc_native(session_id, host)
            return
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        _reject_reserved_codex_command(text)
        queue = self._add_codex_event_subscriber(session_id, session)
        turn_id: str | None = None
        try:
            turn_id = await self._codex.send(
                session,
                text,
                before_turn_start=lambda attached: self._writeback_codex_before_turn(
                    session_id, attached
                ),
            )
            # turn 接受后再写回已提交的有效设置。
            self._writeback_codex_native(session_id, session)
        except TurnConflictError as exc:
            self._remove_codex_event_subscriber(session_id, queue)
            raise SessionConflictError(str(exc)) from exc
        except SessionHubError:
            self._remove_codex_event_subscriber(session_id, queue)
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为 502，不能落入 500。
            self._remove_codex_event_subscriber(session_id, queue)
            _log.warning("codex turn start failed for %s: %s", session_id, exc)
            raise RuntimeTurnError(f"codex turn failed: {exc}") from exc
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

    async def start_codex_turn(self, session_id: str, text: str) -> str:
        """向指定 Codex 会话发送一条输入并启动新一轮处理。

        函数在 Codex 接受请求后返回，不等待这一轮结束；后续事件由
        subscribe_codex_events 返回。

        Args:
            session_id: 接收输入的 Codex 会话 ID。
            text: 要发送给 Codex 的文字内容。

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

        binding = self._require(session_id)
        reservation = self._reserve_delegate_turn(binding)
        try:
            session = self._require_codex_session(session_id)
            codex = self._require_codex_runtime()
            _reject_reserved_codex_command(text)
            try:
                turn_id = await codex.send(
                    session,
                    text,
                    before_turn_start=lambda attached: (
                        self._writeback_codex_before_turn(session_id, attached)
                    ),
                )
                self._writeback_codex_native(session_id, session)
                return turn_id
            except TurnConflictError as exc:
                raise SessionConflictError(str(exc)) from exc
            except SessionHubError:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.warning("codex turn start failed for %s: %s", session_id, exc)
                raise RuntimeTurnError(f"codex turn failed: {exc}") from exc
        finally:
            self._capacity.release_turn(reservation)

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
        task = self._codex_event_tasks.get(session_id)
        if task is None or task.done():
            self._codex_event_tasks[session_id] = asyncio.create_task(
                self._pump_codex_events(session_id, session),
                name=f"codex-events-{session_id}",
            )
        return queue

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
                for queue in tuple(self._codex_event_subscribers.get(session_id, ())):
                    queue.put_nowait(payload)
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
        self._lifecycle.remember_delegate_identity(binding, cc_session_id)
        try:
            updated = self._store.update_native(
                session_id,
                native_session_id=cc_session_id,
                model=model,
                effort=getattr(host, "effort", None),
                permission=getattr(host, "permission_mode", None),
            )
            self._persist_native_title(updated)
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
        self._lifecycle.remember_delegate_identity(binding, thread_binding.thread_id)
        try:
            sandbox = getattr(thread_binding, "effective_sandbox", None)
            approval = getattr(thread_binding, "effective_approval", None)
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
        except KeyError:
            _log.debug("codex writeback skipped, binding %s gone", session_id)

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
