"""维护一个 Trowel 会话对应的 Codex thread 状态、绑定和事件队列。

进程与传输由共享 manager 管理，多个会话可以复用同一个 app-server。
初始有效事实来自 thread/start 或 thread/resume，后续接受的 turn 设置再更新绑定。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping

from trowel_py.codex_host.errors import CodexHostError
from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    HostStatusKind,
    TranslatedItem,
    host_status_item,
    immutable_payload,
)
from trowel_py.codex_host.session_types import (
    CodexSessionConfig,
    ThreadBinding,
    TrowelMemoryMcpConfig as TrowelMemoryMcpConfig,
    TrowelAgentMcpConfig as TrowelAgentMcpConfig,
    build_default_trowel_agent_mcp as build_default_trowel_agent_mcp,
    build_default_trowel_memory_mcp as build_default_trowel_memory_mcp,
    parse_thread_binding,
)

_log = logging.getLogger(__name__)


class TurnConflictError(CodexHostError):
    """当前操作与会话或原生 thread 的生命周期状态冲突。"""


class CodexSessionState(str, Enum):
    """会话级 turn 状态；WAITING 预留给审批和用户输入暂停。"""

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


# RUNNING 与 WAITING 拒绝新发送；INTERRUPTED 与 FAILED 允许重新发送。
_SENDABLE_STATES: frozenset[CodexSessionState] = frozenset(
    {CodexSessionState.IDLE, CodexSessionState.INTERRUPTED, CodexSessionState.FAILED}
)


class CodexSession:
    """一个 Trowel 会话对应的 Codex thread 状态机与事件队列。"""

    def __init__(
        self,
        config: CodexSessionConfig,
        *,
        event_sink: Callable[[CodexEvent, ThreadBinding | None], None] | None = None,
    ) -> None:
        self._config = config
        self._event_sink = event_sink
        # resume 先放入最小绑定以选择 thread/resume，响应回来后再覆盖真实事实。
        self._binding: ThreadBinding | None
        if config.initial_thread_id is not None:
            self._binding = ThreadBinding(
                thread_id=config.initial_thread_id,
                model="",
                model_provider="",
                cwd=config.workdir,
                sandbox=MappingProxyType({}),
                approval_policy=None,
            )
        else:
            self._binding = None
        self._current_turn_id: str | None = None
        self._state: CodexSessionState = CodexSessionState.IDLE
        self._seq: int = 0
        self._session_started_emitted: bool = False
        # begin_send 到 RUNNING 之间也必须拒绝并发发送。
        self._sending: bool = False
        # turn/start 响应后的抢先通知先缓存，保证 USER 与 TURN_STARTED 排在前面。
        self._turn_started: bool = False
        self._has_started_turn: bool = False
        self._pending: list[TranslatedItem] = []
        self._queue: asyncio.Queue[CodexEvent] = asyncio.Queue()
        self._pending_turn_settings: tuple[str, str] | None = None
        # 暂存 (approval, sandbox) preset 字符串对，在下次 turn/start 作为 override
        # 发送；permission 与 model/effort 是独立维度，不与 _pending_turn_settings 合并。
        self._pending_permission_override: tuple[str | None, str | None] | None = None

    @property
    def config(self) -> CodexSessionConfig:
        return self._config

    @property
    def session_id(self) -> str:
        return self._config.trowel_session_id

    @property
    def thread_id(self) -> str | None:
        return self._binding.thread_id if self._binding is not None else None

    @property
    def binding(self) -> ThreadBinding | None:
        return self._binding

    @property
    def current_turn_id(self) -> str | None:
        return self._current_turn_id

    @property
    def has_in_flight_turn(self) -> bool:
        """判断是否存在需要在 host EOF 时结束的 turn。

        范围包括 begin_send 到 record_turn_started 的窗口，不能只检查 RUNNING。
        """

        return (
            self._sending
            or self._current_turn_id is not None
            or self._state is CodexSessionState.RUNNING
        )

    @property
    def state(self) -> CodexSessionState:
        return self._state

    @property
    def is_new_thread(self) -> bool:
        return self._binding is None

    def queue_turn_settings(self, model: str, effort: str) -> None:
        """为下一个 turn 暂存原子 model/effort 对；活动 turn 期间拒绝修改。"""

        if self._sending or self._state not in _SENDABLE_STATES:
            raise TurnConflictError(
                f"session {self.session_id} cannot change settings in state "
                f"{self._state.name}"
            )
        self._pending_turn_settings = (model, effort)

    def next_turn_settings(self) -> tuple[str | None, str | None]:
        """返回下一个 turn 的原子设置对。

        初次发送回退到会话配置，后续没有暂存设置时返回空对。
        """

        if self._pending_turn_settings is not None:
            return self._pending_turn_settings
        if not self._has_started_turn:
            return self._config.model, self._config.effort
        return None, None

    @property
    def can_queue_permission_override(self) -> bool:
        """当前是否允许排队 permission override（活动 turn 期间不允许）。

        供 PATCH 路径在持久化前做只读检查，避免 ``BindingStore.put`` 已写盘、
        ``queue_permission_override`` 再抛错的部分成功。
        """

        return not self._sending and self._state in _SENDABLE_STATES

    def queue_permission_override(
        self, *, approval: str | None, sandbox: str | None
    ) -> None:
        """为下一个 turn 暂存 permission preset；活动 turn 期间拒绝修改。

        ``approval`` 与 ``sandbox`` 取自 ``_CODEX_PERMISSION_PRESETS`` 映射，
        保持 preset 的原子语义；``follow`` preset 排队 ``(None, None)`` 覆盖
        之前的非空请求。override 在 ``commit_turn_settings`` 后清空。
        """

        if not self.can_queue_permission_override:
            raise TurnConflictError(
                f"session {self.session_id} cannot change permission in state "
                f"{self._state.name}"
            )
        self._pending_permission_override = (approval, sandbox)

    def next_turn_permission_override(self) -> tuple[str | None, str | None]:
        """返回下一个 turn 的 permission override。

        首次发送回退到会话配置的 approval/sandbox；已有活动 turn 且未暂存时
        返回 ``(None, None)``，表示不向原生发送 override 字段。
        """

        if self._pending_permission_override is not None:
            return self._pending_permission_override
        if not self._has_started_turn:
            return self._config.approval_policy, self._config.sandbox
        return None, None

    def apply_permission_override(
        self, *, approval: str | None, sandbox: str | None
    ) -> None:
        """同步替换 live ``config`` 的 approval/sandbox，让重连读取新值。

        与 ``queue_permission_override`` 互补：queue 写 ``_pending`` 供下次
        ``turn/start`` override 使用、活动 turn 期间拒绝；apply 直接替换
        ``_config``，影响 ``thread_resume_params``（重连路径）的输出，不参与
        活动 turn 的并发仲裁——只在 PATCH 路径成功 queue 之后调用。

        重连路径只读 ``_config``：若只更新 ``_pending`` 与持久 binding，
        ``thread_resume_params`` 仍输出旧值，host 重连会用旧 approval/sandbox
        覆盖用户刚选的权限。``None`` 入参表示清空，让重连不再发送 override。
        """

        self._config = replace(
            self._config,
            approval_policy=approval,
            sandbox=sandbox,
        )

    def commit_turn_settings(
        self, *, model: str | None, effort: str | None
    ) -> CodexEvent | None:
        """仅在 turn/start 被接受后提交设置，并按需发出 model_changed。"""

        if self._binding is None:
            return None
        # turn/start 已被原生接受，permission override 已随请求生效；无论本次
        # 是否修改 model/effort，pending permission 都必须在提交窗口清空。
        self._pending_permission_override = None
        if model is None and effort is None:
            return None
        self._binding = replace(
            self._binding,
            model=model if model is not None else self._binding.model,
            reasoning_effort=(
                effort if effort is not None else self._binding.reasoning_effort
            ),
        )
        self._pending_turn_settings = None
        return self._emit(
            TranslatedItem(
                type=CodexEventType.MODEL_CHANGED,
                thread_id=self._binding.thread_id,
                payload=immutable_payload(
                    model=self._binding.model,
                    effort=self._binding.reasoning_effort,
                ),
            )
        )

    def begin_send(self) -> None:
        """为新 turn 预留会话；已有活动或启动中的 turn 时拒绝。"""

        if self._sending or self._state not in _SENDABLE_STATES:
            raise TurnConflictError(
                f"session {self.session_id} cannot accept a new turn in state "
                f"{self._state.name} (sending={self._sending})"
            )
        self._sending = True
        self._turn_started = False
        self._pending = []

    def abort_send(self) -> None:
        """启动编排提前失败时释放发送预留，同时保留待提交设置。"""

        self._sending = False
        self._turn_started = False
        self._pending = []

    def attach_thread_binding(self, result: Mapping[str, Any]) -> ThreadBinding:
        """用最新原生响应覆盖绑定；服务端事实始终优先。"""

        binding = parse_thread_binding(result)
        self._binding = binding
        return binding

    def emit_session_started_if_first(self) -> CodexEvent | None:
        """每个会话只发出一次包含有效事实的 SESSION_STARTED。"""

        if self._session_started_emitted or self._binding is None:
            return None
        self._session_started_emitted = True
        binding = self._binding
        item = TranslatedItem(
            type=CodexEventType.SESSION_STARTED,
            thread_id=binding.thread_id,
            payload=immutable_payload(
                model=binding.model,
                model_provider=binding.model_provider,
                cwd=binding.cwd,
                service_tier=binding.service_tier,
                reasoning_effort=binding.reasoning_effort,
                sandbox=dict(binding.sandbox),
                approval_policy=(
                    dict(binding.approval_policy)
                    if isinstance(binding.approval_policy, Mapping)
                    else binding.approval_policy
                ),
                permission_profile=binding.permission_profile,
                effective_sandbox=binding.effective_sandbox,
                effective_approval=binding.effective_approval,
                network_access=binding.network_access,
            ),
        )
        return self._emit(item)

    def record_turn_started(self, turn_id: str, user_text: str) -> list[CodexEvent]:
        """本地发出 USER 与 TURN_STARTED，并进入 RUNNING。

        Codex 不回显本轮用户输入，因此 USER 事件必须由会话补齐。
        """

        if self._binding is None:
            raise TurnConflictError(
                f"session {self.session_id} cannot start a turn with no thread binding"
            )
        thread_id = self._binding.thread_id
        user_event = self._emit(
            TranslatedItem(
                type=CodexEventType.USER,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=immutable_payload(text=user_text),
            )
        )
        turn_event = self._emit(
            TranslatedItem(
                type=CodexEventType.TURN_STARTED,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=immutable_payload(),
            )
        )
        self._current_turn_id = turn_id
        self._has_started_turn = True
        self._state = CodexSessionState.RUNNING
        self._sending = False
        self._turn_started = True
        # 抢先通知必须在 TURN_STARTED 后按原顺序落队并更新终态。
        flushed: list[CodexEvent] = []
        for pending_item in self._pending:
            flushed.append(self._emit(pending_item))
            self._apply_terminal_state(pending_item)
        self._pending = []
        return [user_event, turn_event, *flushed]

    def emit_translated(self, item: TranslatedItem) -> CodexEvent | None:
        """处理翻译后的通知；pre-turn 窗口先缓存，记录 turn 后再顺序发出。"""

        if self._sending and not self._turn_started:
            self._pending.append(item)
            return None
        event = self._emit(item)
        self._apply_terminal_state(item)
        return event

    def mark_host_exited(
        self, reason: str, *, exit_code: int | None = None
    ) -> CodexEvent:
        """为活动 turn 合成 HOST_EXITED 终态，同时保留 thread 绑定以供恢复。"""

        self._sending = False
        self._turn_started = False
        self._pending = []
        running = self._state == CodexSessionState.RUNNING
        self._current_turn_id = None
        self._state = CodexSessionState.FAILED
        return self._emit(
            host_status_item(
                HostStatusKind.HOST_EXITED,
                thread_id=self.thread_id,
                reason=reason,
                exit_code=exit_code,
            ),
            also_terminal=running,
        )

    def emit_host_status(
        self, status: HostStatusKind, *, reason: str | None = None
    ) -> CodexEvent:
        return self._emit(
            host_status_item(status, thread_id=self.thread_id, reason=reason)
        )

    def drain(self) -> list[CodexEvent]:
        """非阻塞地取出队列中的全部事件。"""

        out: list[CodexEvent] = []
        while not self._queue.empty():
            out.append(self._queue.get_nowait())
        return out

    async def events(self) -> AsyncIterator[CodexEvent]:
        """从当前会话的独立队列按序持续产出事件。"""

        while True:
            event = await self._queue.get()
            yield event

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _stamp(self, item: TranslatedItem) -> CodexEvent:
        return CodexEvent(
            session_id=self._config.trowel_session_id,
            seq=self._next_seq(),
            type=item.type,
            thread_id=item.thread_id,
            turn_id=item.turn_id,
            item_id=item.item_id,
            payload=item.payload,
        )

    def _emit(self, item: TranslatedItem, *, also_terminal: bool = False) -> CodexEvent:
        event = self._stamp(item)
        if self._event_sink is not None:
            try:
                self._event_sink(event, self._binding)
            except Exception:  # noqa: BLE001 - memory 旁路失败不能打断原生 turn。
                _log.warning(
                    "Codex turn journal failed for session=%s turn=%s; "
                    "turn remains unsealed for memory",
                    self.session_id,
                    event.turn_id,
                    exc_info=True,
                )
        self._queue.put_nowait(event)
        return event

    def _apply_terminal_state(self, item: TranslatedItem) -> None:
        """应用 turn 终态；native_error 只上报，不结束仍可能重试的 turn。"""

        if item.type is CodexEventType.FINISHED:
            self._current_turn_id = None
            self._state = CodexSessionState.IDLE
        elif item.type is CodexEventType.INTERRUPTED:
            self._current_turn_id = None
            self._state = CodexSessionState.INTERRUPTED
        elif item.type is CodexEventType.ERROR:
            if item.payload.get("kind") == "native_error":
                return
            self._current_turn_id = None
            self._state = CodexSessionState.FAILED
