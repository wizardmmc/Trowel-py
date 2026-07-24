"""协调 CC 与 Codex 会话的绑定、路由和生命周期。

持久化 binding 是会话创建后唯一的 runtime 路由依据；模型和界面状态都不能代替它。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, Callable

from trowel_py.agent_host.binding import Runtime, SessionBinding, make_binding
from trowel_py.agent_host.cc_adapter import CcEventAdapter
from trowel_py.agent_host.codex_adapter import CodexEventAdapter
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
from trowel_py.codex_host.pending_requests import (
    PendingRequestConflictError,
    PendingRequestDecisionError,
    PendingRequestNotFoundError,
    PendingRequestOwnershipError,
)
from trowel_py.cc_host.session_lifecycle import (
    CcCapacityError,
    CcWorkdirNotFoundError,
)
from trowel_py.agent_host.store import BindingStore
from trowel_py.agent_host.events import AgentEvent

_log = logging.getLogger(__name__)

# capability 是界面的能力发现契约，界面不能从 runtime 推断功能。
CC_CAPABILITIES: tuple[str, ...] = ("tools", "approval", "checkpoint", "workflow")
CODEX_CAPABILITIES: tuple[str, ...] = ("tools", "approval")

# 连接上限按仍有 binding 的已注册 session/thread 计数，共享 manager 不合并名额。
MAX_CONNECTIONS = 20
# 公开兼容常量；Hub 当前未执行 running gate。
MAX_RUNNING = 5

_TURN_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error"})


class SessionHubError(Exception):
    """SessionHub 拒绝命令或无法完成 runtime 操作。"""


class InvalidSessionRequestError(SessionHubError):
    """创建或操作请求不满足基本输入条件。"""


class SessionNotFoundError(SessionHubError):
    """binding 或对应的原生会话不存在。"""


class SessionAccessError(SessionHubError):
    """调用方试图操作不属于当前会话的资源。"""


class SessionConflictError(SessionHubError):
    """命令与当前会话、容量或并发状态冲突。"""


class SessionOperationError(SessionHubError):
    """命令不适用于当前 runtime 或参数组合。"""


class RuntimeUnavailableError(SessionHubError):
    """目标 runtime host 当前不可用。"""


class RuntimeTurnError(SessionHubError):
    """runtime 未能启动或持久化当前 turn。"""


class RuntimeFrozenError(SessionOperationError):
    """runtime 已成为路由身份，创建后不能修改。"""


class CrossRuntimeResumeError(SessionConflictError):
    """同一原生会话 id 不能跨 runtime 恢复。"""


class ConditionMismatchError(SessionConflictError):
    """恢复同一原生会话时不能改变已冻结的注入条件。"""


# 生产 opener 与测试替身共享调用协议但具体类型不同，因此保持宽松 Callable。
CcOpener = Callable[..., Any]


def _default_cc_registry() -> dict[str, Any]:
    from trowel_py.cc_host import routes as cc_routes

    return cc_routes.get_registry()


def _default_cc_opener() -> CcOpener:
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
    ) -> None:
        self._store = store
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
        self._active_id: str | None = None
        # adapter 跨 turn 复用；被 adapter 丢弃的原生事件不占统一序号。
        self._cc_adapters: dict[str, CcEventAdapter] = {}
        self._codex_adapters: dict[str, CodexEventAdapter] = {}

    @property
    def store(self) -> BindingStore:
        return self._store

    @property
    def codex_available(self) -> bool:
        return self._codex is not None

    def create(self, req: CreateAgentSessionRequest) -> SessionBinding:
        if not Path(req.workdir).is_dir():
            raise InvalidSessionRequestError("workdir does not exist")
        if self._live_connection_count() >= MAX_CONNECTIONS:
            raise SessionConflictError(
                f"连接数已达上限（{MAX_CONNECTIONS}），请先关闭一些 session"
            )
        if req.runtime == "claude_code":
            return self._create_cc(req)
        return self._create_codex(req)

    def _create_cc(self, req: CreateAgentSessionRequest) -> SessionBinding:
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
        )
        try:
            opened = self._cc_opener(
                cc_req,
                self._cc_registry,
                proxy_base_url=self._cc_proxy_base_url,
                settings_path=self._cc_settings_path,
            )
        except CcWorkdirNotFoundError as exc:
            raise InvalidSessionRequestError(str(exc)) from exc
        except CcCapacityError as exc:
            raise SessionConflictError(str(exc)) from exc
        binding = make_binding(
            session_id=opened.sid,
            runtime=Runtime.CLAUDE_CODE,
            native_session_id=req.resume_from,
            workdir=req.workdir,
            model=req.model,
            effort=req.effort,
            permission=req.permission_mode,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            self_enabled=req.self_enabled,
            capabilities=CC_CAPABILITIES,
            name=opened.name,
        )
        self._store.put(binding)
        self._active_id = opened.sid
        return binding

    def _create_codex(self, req: CreateAgentSessionRequest) -> SessionBinding:
        """``resume_from`` 只登记原生 thread，首次 turn 才执行恢复。"""

        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        self._refuse_on_memory_mcp_collision(req.workdir)
        prepared = prepare_codex_session(
            req,
            session_id_factory=lambda: uuid.uuid4().hex,
            permission_presets=_CODEX_PERMISSION_PRESETS,
            fingerprint=_injection_fingerprint,
        )
        sid = prepared.session_id
        if self._codex is not None:
            self._codex.register(prepared.session)
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
            capabilities=CODEX_CAPABILITIES,
            name=self._display_name(req.workdir),
            permission_preset=prepared.permission_preset,
            injection_hash=prepared.injection_hash,
            declared_mcp_roster=prepared.declared_mcp_roster,
        )
        self._store.put(binding)
        self._active_id = sid
        return binding

    def _refuse_on_memory_mcp_collision(self, workdir: str) -> None:
        """任一受检配置层存在同名 MCP 时都无法保证 memory-off 隔离。"""

        from trowel_py.codex_host.mcp_isolation import find_conflicting_mcp_server

        conflict = find_conflicting_mcp_server(
            codex_home=self._codex_config_home,
            workdir=workdir,
        )
        if conflict is not None:
            raise SessionConflictError(
                f"a Codex MCP server named {conflict.server_name!r} is "
                f"already declared in {conflict.config_path}; trowel "
                f"cannot guarantee memory-off isolation. Rename or remove "
                f"that entry and retry."
            )

    def _display_name(self, workdir: str) -> str:
        basename = Path(workdir).name or str(workdir)
        same_workdir = sum(1 for b in self._store.list_all() if b.workdir == workdir)
        return basename if same_workdir == 0 else f"{basename} #{same_workdir + 1}"

    def get(self, session_id: str) -> SessionBinding | None:
        return self._store.get(session_id)

    async def list_codex_models(self) -> list[dict[str, Any]]:
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
        """合并两个 runtime 的最新摘要，并使用 Trowel 自有 offset 游标。"""

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
        cc_summaries = await asyncio.to_thread(
            scan_cc_history, workdir, limit=required
        )
        codex_threads: list[dict[str, Any]] = []
        if self._codex is not None:
            codex_threads = await self._codex.list_threads(cwd=workdir, limit=required)
        return merge_history_page(
            cc_summaries,
            codex_threads,
            offset=offset,
            limit=limit,
        )

    async def history(self, session_id: str) -> list[dict[str, Any]]:
        """按 binding runtime 回放公开的原生历史，并从序号 1 重新封装。"""

        binding = self._require(session_id)
        native_session_id = binding.native_session_id
        if not native_session_id:
            return []
        if binding.runtime is Runtime.CLAUDE_CODE:
            from trowel_py.cc_host.history import parse_history

            events = await asyncio.to_thread(
                parse_history, binding.workdir, native_session_id
            )
            adapter = CcEventAdapter(session_id)
            return [
                adapter.wrap(event.model_dump()).model_dump(by_alias=True)
                for event in events
            ]
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        from trowel_py.codex_host.history import events_from_thread

        thread = await self._codex.read_thread(native_session_id)
        adapter = CodexEventAdapter(session_id)
        envelopes = []
        for event in events_from_thread(session_id, thread):
            envelope = adapter.wrap(event)
            if envelope is not None:
                envelopes.append(envelope.model_dump(by_alias=True))
        return envelopes

    def _require(self, session_id: str) -> SessionBinding:
        binding = self._store.get(session_id)
        if binding is None:
            raise SessionNotFoundError(f"session {session_id} not found")
        return binding

    def list_active(self) -> tuple[list[dict[str, Any]], str | None]:
        """返回 binding 列表，并以本地 registry/manager 的 session 状态覆盖持久化值。"""

        items: list[dict[str, Any]] = []
        for binding in self._store.list_all():
            item = binding.to_dict()
            connected, running = self._live_status(binding)
            item["connected"] = connected
            item["running"] = running
            items.append(item)
        return items, self._active_id

    def _live_status(self, binding: SessionBinding) -> tuple[bool, bool]:
        if binding.runtime is Runtime.CLAUDE_CODE:
            host = self._cc_registry.get(binding.session_id)
            if host is None:
                return False, False
            return (not getattr(host, "is_dead", True)), bool(
                getattr(host, "running", False)
            )
        if self._codex is None:
            return False, False
        session = self._codex.get_session(binding.session_id)
        if session is None:
            return False, False
        state = getattr(session, "state", None)
        state_value = getattr(state, "value", state)
        return True, state_value == "running"

    def activate(self, session_id: str) -> str:
        """切换当前视图；CC 还需同步旧 routes 的 active id。"""

        binding = self._require(session_id)
        self._active_id = session_id
        if binding.runtime is Runtime.CLAUDE_CODE:
            from trowel_py.cc_host import routes as cc_routes

            cc_routes.set_active_session_id(session_id)
        return session_id

    def patch(self, session_id: str, **fields: Any) -> None:
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
        """暂存下一个 Codex turn 的原子设置对；不支持的 effort 回落到原生默认值。"""

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
        """注销运行时会话并删除 binding；未知 id 返回 False，允许重试。"""

        binding = self._store.get(session_id)
        if binding is None:
            return False
        if binding.runtime is Runtime.CLAUDE_CODE:
            # 复用旧 closer，保持 registry、多开索引与 active id 一致。
            from trowel_py.cc_host import routes as cc_routes

            await cc_routes.close_cc_session(session_id, self._cc_registry)
        elif self._codex is not None:
            self._codex.unregister(session_id)
        # 删除 adapter，避免复用 id 继承旧序号。
        self._cc_adapters.pop(session_id, None)
        self._codex_adapters.pop(session_id, None)
        self._store.delete(session_id)
        if self._active_id == session_id:
            self._active_id = None
        return True

    async def stream(self, session_id: str, text: str) -> AsyncIterator[dict[str, Any]]:
        """按 binding 产出统一事件；Codex 遇终态结束，CC 随 send 返回结束。"""

        binding = self._require(session_id)
        if binding.runtime is Runtime.CLAUDE_CODE:
            host = self._cc_registry.get(session_id)
            if host is None:
                raise SessionNotFoundError(f"cc session {session_id} not live")
            cc_adapter = self._cc_adapters.get(session_id)
            if cc_adapter is None:
                cc_adapter = CcEventAdapter(session_id)
                self._cc_adapters[session_id] = cc_adapter
            async for event in host.send(text):
                raw = dict(event) if isinstance(event, dict) else event.model_dump()
                envelope = cc_adapter.wrap(raw).model_dump(by_alias=True)
                self._observe(envelope)
                yield envelope
            self._writeback_cc_native(session_id, host)
            return
        if self._codex is None:
            raise RuntimeUnavailableError("codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"codex session {session_id} not live")
        try:
            await self._codex.send(
                session,
                text,
                before_turn_start=lambda attached: self._writeback_codex_before_turn(
                    session_id, attached
                ),
            )
            # turn 接受后再写回已提交的有效设置。
            self._writeback_codex_native(session_id, session)
        except SessionHubError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为 502，不能落入 500。
            _log.warning("codex turn start failed for %s: %s", session_id, exc)
            raise RuntimeTurnError(f"codex turn failed: {exc}") from exc
        codex_adapter = self._codex_adapters.get(session_id)
        if codex_adapter is None:
            codex_adapter = CodexEventAdapter(session_id)
            self._codex_adapters[session_id] = codex_adapter
        async for event in session.events():
            codex_event = codex_adapter.wrap(event)
            if codex_event is None:
                # adapter 丢弃的事件不占统一序号，避免产生空洞。
                continue
            payload = codex_event.model_dump(by_alias=True)
            self._observe(payload)
            yield payload
            if _is_terminal(payload):
                break

    def _observe(self, payload: Mapping[str, Any]) -> None:
        """observer 是旁路消费者；其异常只能记录，不能中断用户 turn。"""

        if self._event_observer is None:
            return
        try:
            self._event_observer(payload)
        except Exception:
            _log.warning("[hub] event observer raised; ignored", exc_info=True)

    def error_envelope(self, session_id: str, detail: Any) -> dict[str, Any]:
        """从会话自身序号空间构造终止错误。

        binding 消失后无法恢复 runtime 与连续序号，只能返回 legacy 降级帧。
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
                cc_adapter = CcEventAdapter(session_id)
                self._cc_adapters[session_id] = cc_adapter
            return cc_adapter.error_event(detail).model_dump(by_alias=True)
        codex_adapter = self._codex_adapters.get(session_id)
        if codex_adapter is None:
            codex_adapter = CodexEventAdapter(session_id)
            self._codex_adapters[session_id] = codex_adapter
        return codex_adapter.error_event(detail).model_dump(by_alias=True)

    def _writeback_cc_native(self, session_id: str, host: Any) -> None:
        cc_session_id = getattr(host, "cc_session_id", None)
        model = getattr(host, "model", None)
        if cc_session_id is None and model is None:
            return
        try:
            self._store.update_native(
                session_id,
                native_session_id=cc_session_id,
                model=model,
            )
        except KeyError:
            _log.debug("cc writeback skipped, binding %s gone", session_id)

    def _writeback_codex_native(self, session_id: str, session: Any) -> None:
        """只写回完整原生事实；空模型 placeholder 不得覆盖已有 binding。"""

        thread_binding = getattr(session, "binding", None)
        if thread_binding is None or not thread_binding.model:
            return
        try:
            sandbox = getattr(thread_binding, "effective_sandbox", None)
            approval = getattr(thread_binding, "effective_approval", None)
            self._store.update_native(
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
        except KeyError:
            _log.debug("codex writeback skipped, binding %s gone", session_id)

    def _writeback_codex_before_turn(self, session_id: str, session: Any) -> None:
        """原生 turn 启动前必须能写回并重新读取 binding。"""

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

    def _live_connection_count(self) -> int:
        cc_live = sum(
            1 for sid in self._cc_registry if self._store.get(sid) is not None
        )
        codex_live = 0
        if self._codex is not None:
            codex_live = sum(
                1 for sid in self._codex.session_ids if self._store.get(sid) is not None
            )
        return cc_live + codex_live


def _is_terminal(payload: dict[str, Any]) -> bool:
    """判断统一事件是否结束 Codex 流。

    native_error 已由 adapter 映射为非终态 retrying；host_exited 则是特殊的
    host_status 终态。
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
    """为旧 permission 展示字段拼接兼容标签。"""

    labels = {
        "read-only": "Read only",
        "workspace-write": "Workspace write",
        "danger-full-access": "Full access",
    }
    if sandbox is None and approval is None:
        return None
    sandbox_label = labels.get(sandbox or "", sandbox or "Unknown sandbox")
    return f"{sandbox_label} · {approval or 'unknown approval'}"
