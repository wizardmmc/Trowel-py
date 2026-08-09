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
    CodexSessionConfig as CodexSessionConfig,
    ThreadBinding as ThreadBinding,
    TrowelMemoryMcpConfig as TrowelMemoryMcpConfig,
    TrowelAgentMcpConfig as TrowelAgentMcpConfig,
    build_default_trowel_agent_mcp as build_default_trowel_agent_mcp,
    build_default_trowel_memory_mcp as build_default_trowel_memory_mcp,
    parse_thread_binding as parse_thread_binding,
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
    """协调一个 Trowel 会话的 Codex thread 绑定、turn 状态和有序事件队列。

    进程和协议传输由 manager 共享，本对象只保存会话级事实与待提交设置。
    """

    def __init__(
        self,
        config: CodexSessionConfig,
        *,
        event_sink: Callable[[CodexEvent, ThreadBinding | None], None] | None = None,
    ) -> None:
        """创建会话状态机及其独立事件队列。

        Args:
            config: 会话身份、thread 恢复入口和原生启动配置。
            event_sink: 每个事件入队前调用的可选旁路接收器；异常只记录告警，
                不阻断事件入队。
        """

        self._config = config
        self._event_sink = event_sink
        # 初始 thread ID 只构成 resume 路由所需的占位绑定；原生响应随后覆盖其余事实。
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
        # 显式发送的抢先 turn/started 只记录 ID；record_turn_started、
        # record_autonomous_turn_started 或 abort_send 结束预留前拒绝并发发送。
        self._sending: bool = False
        self._autonomous_start: bool = False
        self._memory_eligible: bool = True
        # 发送预留期间缓存普通翻译事件，待对应 TURN_STARTED 入队后再按序发出。
        self._turn_started: bool = False
        self._native_turn_started_id: str | None = None
        self._has_started_turn: bool = False
        self._pending: list[TranslatedItem] = []
        self._queue: asyncio.Queue[CodexEvent] = asyncio.Queue()
        self._pending_turn_settings: tuple[str, str] | None = None
        # 权限 preset 与 model/effort 分别暂存和清理，避免更新一组时覆盖另一组。
        self._pending_permission_override: tuple[str | None, str | None] | None = None

    @property
    def config(self) -> CodexSessionConfig:
        """返回当前会话配置；权限更新会用新实例替换该值。"""

        return self._config

    @property
    def session_id(self) -> str:
        """返回跨 thread 重连和权限配置替换保持不变的 Trowel 会话 ID。"""

        return self._config.trowel_session_id

    @property
    def thread_id(self) -> str | None:
        """返回当前绑定的 Codex 线程 ID；尚未绑定时为空。"""

        return self._binding.thread_id if self._binding is not None else None

    @property
    def binding(self) -> ThreadBinding | None:
        """返回当前 Codex 线程的已知配置和身份信息。"""

        return self._binding

    @property
    def current_turn_id(self) -> str | None:
        """返回已记录为活动状态的 Codex turn ID；启动预留期间为空。"""

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
        """返回已记录 turn 的状态；发送预留由 ``has_in_flight_turn`` 另行反映。"""

        return self._state

    @property
    def is_new_thread(self) -> bool:
        """判断是否没有 thread 绑定；初始恢复 ID 的占位绑定也视为已有 thread。"""

        return self._binding is None

    def queue_turn_settings(self, model: str, effort: str) -> None:
        """为下一个 turn 暂存不可拆分的 model/effort 设置。

        Args:
            model: 下次 ``turn/start`` 请求使用的模型 ID。
            effort: 与该模型一起提交的推理强度。

        Raises:
            TurnConflictError: 会话正在发送或已有活动 turn。
        """

        if self._sending or self._state not in _SENDABLE_STATES:
            raise TurnConflictError(
                f"session {self.session_id} cannot change settings in state "
                f"{self._state.name}"
            )
        self._pending_turn_settings = (model, effort)

    def next_turn_settings(self) -> tuple[str | None, str | None]:
        """返回下一个 turn 的原子设置对。

        初次发送回退到会话配置，后续没有暂存设置时返回空对。

        Returns:
            应传给下次 ``turn/start`` 的 model/effort；两个 ``None`` 表示省略。
        """

        if self._pending_turn_settings is not None:
            return self._pending_turn_settings
        if not self._has_started_turn:
            return self._config.model, self._config.effort
        return None, None

    @property
    def can_queue_permission_override(self) -> bool:
        """返回当前是否允许为下一个 turn 排队权限覆盖。

        PATCH 路径在持久化前读取此值，避免 binding 已写盘后才因活动 turn 拒绝更新。
        """

        return not self._sending and self._state in _SENDABLE_STATES

    def queue_permission_override(
        self, *, approval: str | None, sandbox: str | None
    ) -> None:
        """为下一个 turn 暂存不可拆分的 approval/sandbox preset。

        ``approval`` 与 ``sandbox`` 取自 ``_CODEX_PERMISSION_PRESETS`` 映射，
        ``follow`` preset 用 ``(None, None)`` 覆盖之前的非空请求。原生
        ``turn/start`` 接受设置后，由 ``commit_turn_settings()`` 清空暂存值。

        Args:
            approval: Codex approval policy 名称；``None`` 表示不覆盖。
            sandbox: Codex sandbox 模式；``None`` 表示不覆盖。

        Raises:
            TurnConflictError: 会话正在发送或已有活动 turn。
        """

        if not self.can_queue_permission_override:
            raise TurnConflictError(
                f"session {self.session_id} cannot change permission in state "
                f"{self._state.name}"
            )
        self._pending_permission_override = (approval, sandbox)

    def next_turn_permission_override(self) -> tuple[str | None, str | None]:
        """返回下一个 turn 的 permission override。

        首次发送回退到会话配置的 approval/sandbox；首次 turn 之后未暂存覆盖时
        返回 ``(None, None)``，表示不向原生发送 override 字段。

        Returns:
            应传给下次 ``turn/start`` 的 approval/sandbox 对。
        """

        if self._pending_permission_override is not None:
            return self._pending_permission_override
        if not self._has_started_turn:
            return self._config.approval_policy, self._config.sandbox
        return None, None

    def apply_permission_override(
        self, *, approval: str | None, sandbox: str | None
    ) -> None:
        """同步替换会话配置中的 approval/sandbox，供重连读取。

        此方法不做活动 turn 仲裁，只在 PATCH 路径成功排队权限覆盖后调用。若只更新
        暂存值和持久 binding，重连仍会从旧会话配置生成 ``thread/resume`` 参数。

        Args:
            approval: 重连时使用的 approval policy；``None`` 表示清空覆盖。
            sandbox: 重连时使用的 sandbox 模式；``None`` 表示清空覆盖。
        """

        self._config = replace(
            self._config,
            approval_policy=approval,
            sandbox=sandbox,
        )

    def commit_turn_settings(
        self, *, model: str | None, effort: str | None
    ) -> CodexEvent | None:
        """在 ``turn/start`` 被接受后提交暂存设置。

        有 thread 绑定时总会清空权限覆盖；model 或 effort 至少一项非空时更新绑定、
        清空 model/effort 暂存值并发出 ``MODEL_CHANGED``。

        Args:
            model: 原生请求接受的模型；``None`` 表示保持绑定中的值。
            effort: 原生请求接受的推理强度；``None`` 表示保持绑定中的值。

        Returns:
            设置模型或推理强度时产生的事件；无绑定或两项均为空时返回 ``None``。
        """

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

    def begin_send(
        self, *, autonomous: bool = False, memory_eligible: bool = True
    ) -> None:
        """为新 turn 建立发送预留；已有活动 turn 或发送预留时拒绝。

        Args:
            autonomous: 预留是否用于没有本地用户消息的原生自主 turn。
            memory_eligible: 自主 turn 是否允许进入 memory 流程。

        Raises:
            TurnConflictError: 已有发送预留或当前状态不允许启动新 turn。
        """

        if self._sending or self._state not in _SENDABLE_STATES:
            raise TurnConflictError(
                f"session {self.session_id} cannot accept a new turn in state "
                f"{self._state.name} (sending={self._sending})"
            )
        self._sending = True
        self._autonomous_start = autonomous
        self._memory_eligible = memory_eligible
        self._turn_started = False
        self._pending = []

    def abort_send(self) -> None:
        """在启动编排失败后结束发送预留，同时保留待提交设置。

        若原生 ``turn/started`` 已先到达，则将其登记为没有 ``USER`` 事件的自主
        turn，先发出 ``TURN_STARTED``，再按原顺序发出缓存通知；否则仅清理本次
        启动暂态。
        """

        native_turn_id = self._native_turn_started_id
        pending = self._pending
        self._sending = False
        self._autonomous_start = False
        self._turn_started = False
        self._native_turn_started_id = None
        self._pending = []
        if native_turn_id is None:
            self._memory_eligible = True
            return
        self.record_native_turn_started(native_turn_id)
        for item in pending:
            self._emit(item)
            self._apply_terminal_state(item)
        self._memory_eligible = True

    def attach_thread_binding(self, result: Mapping[str, Any]) -> ThreadBinding:
        """解析最新原生响应并覆盖当前 thread 绑定。

        Args:
            result: ``thread/start``、``thread/resume`` 或 ``thread/read`` 的结果。

        Returns:
            由服务端事实构成的新绑定。
        """

        binding = parse_thread_binding(result)
        self._binding = binding
        return binding

    def restore_thread_binding_after_failed_attach(
        self,
        previous: ThreadBinding | None,
    ) -> None:
        """在原生挂载补偿成功后恢复挂载前的 thread 占位状态。

        Args:
            previous: 挂载前的绑定；新 thread 为 None，恢复 thread 为仅含 ID 的占位。
        """

        self._binding = previous

    def emit_session_started_if_first(self) -> CodexEvent | None:
        """首次取得 thread 绑定后发出一次 ``SESSION_STARTED``。

        Returns:
            首次发出的事件；尚无绑定或已经发出时返回 ``None``。
        """

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

        Args:
            turn_id: ``turn/start`` 响应中的原生 turn ID。
            user_text: 用于补齐 ``USER`` 事件的本地输入正文。

        Returns:
            本次产生的起始事件及随后按序冲刷的缓存事件。

        Raises:
            TurnConflictError: 尚无 thread 绑定，或响应 ID 与抢先通知不一致。
        """

        if self._binding is None:
            raise TurnConflictError(
                f"session {self.session_id} cannot start a turn with no thread binding"
            )
        if (
            self._native_turn_started_id is not None
            and self._native_turn_started_id != turn_id
        ):
            raise TurnConflictError(
                f"turn/start response {turn_id} does not match native turn/started "
                f"{self._native_turn_started_id}"
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
                payload=immutable_payload(autonomous=False),
            )
        )
        self._current_turn_id = turn_id
        self._has_started_turn = True
        self._state = CodexSessionState.RUNNING
        self._sending = False
        self._autonomous_start = False
        self._memory_eligible = True
        self._turn_started = True
        self._native_turn_started_id = None
        # 抢先通知必须在 TURN_STARTED 后按原顺序落队并更新终态。
        flushed: list[CodexEvent] = []
        for pending_item in self._pending:
            flushed.append(self._emit(pending_item))
            self._apply_terminal_state(pending_item)
        self._pending = []
        return [user_event, turn_event, *flushed]

    def record_native_turn_started(self, turn_id: str) -> CodexEvent | None:
        """处理 app-server 的 ``turn/started`` 通知。

        显式发送预留只保存 ID，等待本地补齐 ``USER``；自主发送预留立即完成；
        没有预留的原生自主 turn 直接发出 ``TURN_STARTED``。重复通知不重复发出
        事件。

        Args:
            turn_id: 通知携带的原生 turn ID。

        Returns:
            自主 turn 的起始事件；显式发送对应的 ``turn/started`` 通知或重复通知
            返回 ``None``。

        Raises:
            TurnConflictError: 尚无 thread 绑定，或另一 turn 已处于活动状态。
        """

        if self._binding is None:
            raise TurnConflictError(
                f"session {self.session_id} cannot start a native turn with no binding"
            )
        if self._sending:
            if self._autonomous_start:
                events = self.record_autonomous_turn_started(turn_id)
                return events[0] if events else None
            self._native_turn_started_id = turn_id
            return None
        if self._current_turn_id == turn_id:
            return None
        if self._state not in _SENDABLE_STATES:
            raise TurnConflictError(
                f"session {self.session_id} received turn {turn_id} while "
                f"{self._current_turn_id or self._state.value} is active"
            )
        event = self._emit(
            TranslatedItem(
                type=CodexEventType.TURN_STARTED,
                thread_id=self._binding.thread_id,
                turn_id=turn_id,
                payload=immutable_payload(
                    autonomous=True,
                    memory_eligible=self._memory_eligible,
                ),
            )
        )
        self._current_turn_id = turn_id
        self._has_started_turn = True
        self._state = CodexSessionState.RUNNING
        self._turn_started = True
        return event

    def record_autonomous_turn_started(self, turn_id: str) -> list[CodexEvent]:
        """记录不合成用户消息的自主 turn。

        有发送预留时结束预留并冲刷缓存；无预留时按原生 ``turn/started`` 通知
        处理。

        Args:
            turn_id: 原生响应或通知确认的 turn ID。

        Returns:
            起始事件及随后按序冲刷的缓存事件；重复通知可能返回空列表。

        Raises:
            TurnConflictError: 尚无 thread 绑定，或响应 ID 与抢先通知不一致。
        """

        if self._binding is None:
            raise TurnConflictError(
                f"session {self.session_id} cannot start a native turn with no binding"
            )
        if not self._sending:
            event = self.record_native_turn_started(turn_id)
            return [event] if event is not None else []
        if (
            self._native_turn_started_id is not None
            and self._native_turn_started_id != turn_id
        ):
            raise TurnConflictError(
                f"native turn response {turn_id} does not match turn/started "
                f"{self._native_turn_started_id}"
            )
        turn_event = self._emit(
            TranslatedItem(
                type=CodexEventType.TURN_STARTED,
                thread_id=self._binding.thread_id,
                turn_id=turn_id,
                payload=immutable_payload(
                    autonomous=True,
                    memory_eligible=self._memory_eligible,
                ),
            )
        )
        self._current_turn_id = turn_id
        self._has_started_turn = True
        self._state = CodexSessionState.RUNNING
        self._sending = False
        self._autonomous_start = False
        self._memory_eligible = True
        self._turn_started = True
        self._native_turn_started_id = None
        flushed: list[CodexEvent] = []
        for pending_item in self._pending:
            flushed.append(self._emit(pending_item))
            self._apply_terminal_state(pending_item)
        self._pending = []
        return [turn_event, *flushed]

    def emit_translated(self, item: TranslatedItem) -> CodexEvent | None:
        """发出翻译后的通知，或在起始事件尚未落队时暂存。

        Args:
            item: 已翻译但尚未补充会话序号的通知。

        Returns:
            已入队的事件；暂存等待 turn 起始事件时返回 ``None``。
        """

        if self._sending and not self._turn_started:
            self._pending.append(item)
            return None
        event = self._emit(item)
        self._apply_terminal_state(item)
        return event

    def emit_child_translated(self, item: TranslatedItem) -> CodexEvent:
        """发出子线程事件，但不改变主线程的 turn 状态。

        Args:
            item: 已翻译的子线程通知。
        """

        return self._emit(item)

    def mark_host_exited(
        self, reason: str, *, exit_code: int | None = None
    ) -> CodexEvent:
        """清理 turn 暂态、将会话标为失败并发出 ``HOST_EXITED``。

        thread 绑定会保留以供恢复；即使没有已记录的活动 turn，也会发出状态事件。

        Args:
            reason: 面向调用方的退出原因。
            exit_code: 原生进程退出码；未知时为 ``None``。
        """

        self._sending = False
        self._autonomous_start = False
        self._memory_eligible = True
        self._turn_started = False
        self._native_turn_started_id = None
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
        """发出 Codex 进程状态事件，不改变当前 turn 状态。

        Args:
            status: 要上报的进程状态。
            reason: 可选的状态原因。
        """

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
        """等待并按序持续产出当前会话的事件。

        Yields:
            队列中的下一个事件；迭代不会自行结束。
        """

        while True:
            event = await self._queue.get()
            yield event

    def _next_seq(self) -> int:
        """按事件发出顺序分配会话内单调递增、从 1 开始的序号。"""

        self._seq += 1
        return self._seq

    def _stamp(self, item: TranslatedItem) -> CodexEvent:
        """为翻译结果补上会话 ID 和递增序号。

        Args:
            item: 尚未绑定 Trowel 会话序号的翻译结果。
        """

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
        """补齐事件身份，尽力写入旁路接收器后放入会话队列。

        Args:
            item: 待发出的翻译结果。
            also_terminal: 兼容参数，当前未使用。
        """

        event = self._stamp(item)
        if self._event_sink is not None:
            try:
                self._event_sink(event, self._binding)
            except Exception:  # noqa: BLE001 - 旁路失败不能打断原生 turn。
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
        """按翻译事件更新当前 turn 的终态。

        ``FINISHED`` 转为 ``IDLE``，``INTERRUPTED`` 转为 ``INTERRUPTED``，
        非 ``native_error`` 的 ``ERROR`` 转为 ``FAILED``；``native_error``
        仅上报并保留活动状态。

        Args:
            item: 可能携带 turn 终态的翻译结果。
        """

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
