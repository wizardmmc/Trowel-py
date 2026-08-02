"""管理每个 trowel 会话独占的长驻 CC stream-json 子进程。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from trowel_py.cc_host import checkpoint, session_registration
from trowel_py.cc_host.input import (
    ExitSession,
    LocalCommand,
    RestartSession,
    SendText,
    UnsupportedSlash,
    classify_input,
)
from trowel_py.cc_host.launcher import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    DEFAULT_PERMISSION_MODE,
    DEFAULT_PERMISSION_PROMPT_TOOL,
    build_args,
    build_subprocess_kwargs,
)
from trowel_py.cc_host.proxy import build_proxy_env, load_settings_env
from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug
from trowel_py.cc_host.stalled import StalledDetector
from trowel_py.cc_host.background_tracker import BackgroundActivityTracker
from trowel_py.cc_host.subagent_usage import (
    merge_usage,
    subagent_transcript_path,
    sum_transcript_usage,
)
from trowel_py.cc_host.translator import Translator
from trowel_py.cc_host.workflow_watcher import WorkflowWatcher

from trowel_py.cc_host.schemas import (
    ElicitationRequestEvent,
    ErrorEvent,
    FinishedEvent,
    LocalCommandEvent,
    ModelChangedEvent,
    SessionExitedEvent,
    SessionStartedEvent,
    StalledWarningEvent,
    StatusEvent,
    SubagentProgressEvent,
    ToolCallEvent,
    TrowelEvent,
    TurnStartEvent,
)
from trowel_py.memory.injection import build_memory_injection
from trowel_py.model_os.self_assembler import build_session_injection
from trowel_py.resource_lifecycle.models import OwnerScope, ProcessIdentity
from trowel_py.resource_lifecycle.processes import ProcessController
from trowel_py.resource_lifecycle.registry import ResourceRegistry

logger = logging.getLogger(__name__)

def _wf_debug(msg: str) -> None:
    """保留 workflow 调试调用点，默认不输出内容。"""
    pass


def _user_msg(text: str) -> bytes:
    """将用户文本编码为 CC stream-json 输入行。"""

    payload = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    return (json.dumps(payload) + "\n").encode()


def _control_response_msg(
    *,
    request_id: str,
    behavior: str,
    updated_input: dict[str, Any] | None = None,
    message: str | None = None,
) -> bytes:
    """编码 `AskUserQuestion` 的 stream-json 回包。

    allow 时 `updated_input` 必须包含 `answers`；deny 可通过 `message`
    说明取消原因。

    Args:
        request_id: 要回答的 CC `control_request` ID。
        behavior: CC 接受的 `allow` 或 `deny` 行为。
        updated_input: allow 回包中的更新后工具输入；不需要时为 `None`。
        message: deny 回包中的说明文字；不需要时为 `None`。

    Returns:
        以换行结尾的 UTF-8 stream-json 输入。
    """
    response: dict[str, Any] = {"behavior": behavior}
    if updated_input is not None:
        response["updatedInput"] = updated_input
    if message is not None:
        response["message"] = message
    payload = {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": response,
        },
    }
    return (json.dumps(payload) + "\n").encode()


async def _default_spawner(args: list[str], kwargs: dict[str, Any]) -> Any:
    """使用 asyncio 启动 CC 子进程。

    Args:
        args: CC 命令行参数。
        kwargs: 传给 `asyncio.create_subprocess_exec` 的启动选项。
    """

    return await asyncio.create_subprocess_exec(*args, **kwargs)


class CCHost:
    """管理单个会话的 CC 子进程与 stream-json 生命周期。"""

    def __init__(
        self,
        session_id: str,
        workdir: str | os.PathLike,
        *,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str = DEFAULT_PERMISSION_MODE,
        permission_prompt_tool: str | None = DEFAULT_PERMISSION_PROMPT_TOOL,
        resume_from: str | None = None,
        proxy_base_url: str | None = None,
        settings_path: Path | str | None = None,
        spawner: Callable[
            [list[str], dict[str, Any]], Awaitable[Any]
        ] = _default_spawner,
        now: Callable[[], float] = time.monotonic,
        stalled_threshold_mild: float = 120.0,
        stalled_threshold_severe: float = 300.0,
        stalled_threshold_kill: float = 1800.0,
        stalled_tick: float = 1.0,
        session_registrar: Any = None,
        session_kind: str = "user",
        agent_mcp_enabled: bool = False,
        mcp_config: str | None = None,
        owned_mcp_config: bool = False,
        memory_enabled: bool = True,
        profile_enabled: bool = True,
        self_enabled: bool = True,
        process_controller: ProcessController | None = None,
        resource_registry: ResourceRegistry | None = None,
        close_interrupt_s: float = 2.0,
        close_term_s: float = 3.0,
        close_kill_s: float = 5.0,
    ) -> None:
        """创建单会话 host 并保存进程、注入和运行时配置。

        构造时不启动 CC；首次 ``send()`` 或 ``start_native()`` 才创建子进程。

        Args:
            session_id: Trowel 分配的会话 ID。
            workdir: CC 子进程使用的工作目录。
            model: 启动 CC 时传入的模型；``None`` 或空字符串使用 Trowel 默认模型。
            effort: 启动 CC 时传入的思考强度；``None`` 或空字符串使用默认值。
            permission_mode: 传给 CC 的权限模式。
            permission_prompt_tool: 处理 CC 权限交互的工具名；``None`` 或空字符串
                表示不注册。
            resume_from: 要继续的原生 CC 会话 ID；``None`` 表示新会话。
            proxy_base_url: CC 请求使用的本地代理地址；``None`` 表示不设置。
            settings_path: 提供 provider 环境变量的 CC settings 文件；``None``
                表示不读取。
            spawner: 接收 argv 和启动选项的异步子进程创建器。
            now: 返回单调时间的函数，用于检测 stdout 静默。
            stalled_threshold_mild: 发布轻度静默警告前的秒数。
            stalled_threshold_severe: 发布严重静默警告前的秒数。
            stalled_threshold_kill: 将静默视为错误并结束轮次前的秒数。
            stalled_tick: 读取 stdout 和检查静默状态的间隔秒数。
            session_registrar: 保存 Memory 会话记录和水位的注册器；``None`` 使用
                默认持久化实现。
            session_kind: 写入 Memory 会话记录的会话来源。
            agent_mcp_enabled: 是否启用跨 Agent 委派工具。
            mcp_config: 候选 MCP 配置文件；启用 Agent MCP 或由本 host 拥有时
                传给 CC，否则忽略。
            owned_mcp_config: 该配置是否由本 host 拥有并在关闭时删除。
            memory_enabled: 是否向会话提供 Memory 内容和读取入口。
            profile_enabled: 是否向会话提供用户画像。
            self_enabled: 是否向会话提供 Trowel 的持续身份信息。
            process_controller: 核验并终止独立进程组的实现；None 保留只操作根进程
                的兼容路径，生产应用会显式传入本机实现。
            resource_registry: 记录 CC 进程组 owner 和关闭终态的应用资源账本。
            close_interrupt_s: 活动 turn 收到 SIGINT 后的最长等待秒数。
            close_term_s: stdin 关闭或 SIGTERM 后的最长等待秒数。
            close_kill_s: SIGKILL 后确认进程组退出的最长等待秒数。
        """

        self.session_id = session_id
        self.workdir = workdir
        # routes 与 hub 会读取；send 或断线 drain 持有 stdout reader 时为 True。
        self.running = False
        # 事件循环只弱引用 task；必须持有 drain，直到它在 finally 中自行释放。
        self._drain_task: asyncio.Task | None = None
        self._model = model or DEFAULT_MODEL
        self._effective_model: str | None = None
        self.effort = effort or DEFAULT_EFFORT
        self.permission_mode = permission_mode
        self._permission_prompt_tool = permission_prompt_tool
        self._resume_from = resume_from
        self._proxy_base_url = proxy_base_url
        self._settings_path = settings_path
        self._spawner = spawner
        self._now = now
        self.stalled_threshold_mild = stalled_threshold_mild
        self.stalled_threshold_severe = stalled_threshold_severe
        self.stalled_threshold_kill = stalled_threshold_kill
        self.stalled_tick = stalled_tick
        self._session_registrar = session_registrar
        self._session_kind = session_kind
        self.agent_mcp_enabled = agent_mcp_enabled
        # 三个开关彼此独立，并在整个会话及重启期间保持不变。
        self._memory_enabled = memory_enabled
        self._profile_enabled = profile_enabled
        self._self_enabled = self_enabled
        # 会话独占的 composite roster 即使为空也走 strict mode；旧的共享 memory-only
        # 配置仍在 memory-off 时丢弃，保持独立 review host 的隔离语义。
        self._mcp_config = (
            mcp_config
            if memory_enabled or agent_mcp_enabled or owned_mcp_config
            else None
        )
        self._owned_mcp_config = owned_mcp_config
        self._process_controller = process_controller
        self._resource_registry = resource_registry
        self._close_interrupt_s = close_interrupt_s
        self._close_term_s = close_term_s
        self._close_kill_s = close_kill_s

        self._proc: Any = None
        self._process_generation = 0
        self._process_resource_id: str | None = None
        self._process_identity: ProcessIdentity | None = None
        self._started = False
        self._cc_session_id: str | None = resume_from
        self._last_finished: FinishedEvent | None = None
        # 锁保证同一 AskUserQuestion 只写入一次 answer 或 cancel。
        self._pending_elicit: dict[str, Any] | None = None
        self._elicit_lock = asyncio.Lock()
        # routes 读取 init 命令列表，作为 slash-items 的名称下限。
        self._init_roster: list[str] = []

        # 首轮复用在首轮执行前保存的 checkpoint，确保第一轮也能回退。
        self._session_start_turn_id = uuid.uuid4().hex
        self._session_start_saved = False
        self._turn_count = 0
        # Workflow 不写 stdout 且可能跨 turn，watcher 必须跨轮读取磁盘状态。
        self._workflow_watcher = WorkflowWatcher(self._workflow_transcript_dir())
        self._bg_tracker = BackgroundActivityTracker()
        # 成功 result 等后台结束；错误 result 立即结束，每轮只发布一个终态。
        self._pending_terminal: FinishedEvent | ErrorEvent | None = None
        if resume_from:
            self._register_session_blocking(str(self._jsonl_path(resume_from)))
        if checkpoint.is_git_repo(self.workdir) and resume_from:
            jsonl_path = self._jsonl_path(resume_from)
            offset = jsonl_path.stat().st_size if jsonl_path.is_file() else 0
            if self._save_session_start_blocking(str(jsonl_path), offset):
                self._session_start_saved = True

    @property
    def cc_session_id(self) -> str | None:
        """返回 CC 原生会话 ID；首次初始化前可能为空。"""

        return self._cc_session_id

    @property
    def has_in_flight_turn(self) -> bool:
        """判断实时发送或断线后的后台 drain 是否仍占用当前轮次。"""

        return self.running or (
            self._drain_task is not None and not self._drain_task.done()
        )

    @property
    def session_kind(self) -> str:
        """返回会话属于用户直接管理还是 Agent 委派。"""

        return self._session_kind

    @property
    def model(self) -> str | None:
        """返回启动 CC 时请求的模型。"""

        return self._model

    @property
    def effective_model(self) -> str | None:
        """返回 CC 初始化或 assistant 消息确认的实际模型。"""

        return self._effective_model

    @property
    def _model_for_display(self) -> str:
        """返回适合本地状态消息展示的模型名称。"""

        return self._model or "(cc default)"

    @property
    def _effort_for_display(self) -> str:
        """返回适合本地状态消息展示的 effort 名称。"""

        return self.effort or "(cc default)"

    @property
    def is_dead(self) -> bool:
        """判断当前 CC 子进程是否不存在或已经退出。"""

        return self._proc is None or self._proc.returncode is not None

    @property
    def memory_enabled(self) -> bool:
        """返回当前会话是否启用 memory。"""

        return self._memory_enabled

    @property
    def profile_enabled(self) -> bool:
        """返回当前会话是否启用 profile 注入。"""

        return self._profile_enabled

    @property
    def self_enabled(self) -> bool:
        """返回当前会话是否启用持续主体注入。"""

        return self._self_enabled

    async def _spawn(self, resume_from: str | None) -> Any:
        # memory 读取失败只能降级注入，不能阻止 CC 启动。
        """组装注入、启动参数和环境后拉起 CC 子进程。

        Memory 或 Self 注入构造失败时记录警告并继续启动。

        Args:
            resume_from: 要继续的原生 CC 会话 ID；`None` 表示新进程不恢复会话。
        """

        try:
            memory_text = build_memory_injection(
                date.today().isoformat(),
                memory_enabled=self._memory_enabled,
                profile_enabled=self._profile_enabled,
            )
        except Exception:
            logger.warning(
                "memory injection failed; cc spawns without the memory section",
                exc_info=True,
            )
            memory_text = ""
        # Self 组装与 memory 读取隔离，前者不能被后者的失败连带关闭。
        try:
            injection = build_session_injection(
                self_enabled=self._self_enabled,
                memory_text=memory_text,
                runtime="cc",
                model=self._model,
                effort=self.effort,
                memory_enabled=self._memory_enabled,
                profile_enabled=self._profile_enabled,
                permission_preset=self.permission_mode,
            )
        except Exception:
            logger.warning(
                "self injection failed; cc spawns without it",
                exc_info=True,
            )
            injection = ""
        args = build_args(
            self.workdir,
            model=self._model,
            effort=self.effort,
            permission_mode=self.permission_mode,
            permission_prompt_tool=self._permission_prompt_tool,
            resume_from=resume_from,
            append_system_prompt=injection,
            mcp_config=self._mcp_config,
        )
        kwargs = build_subprocess_kwargs(
            self.workdir, env=self._build_spawn_env()
        )
        return await self._spawner(args, kwargs)

    def _build_spawn_env(self) -> dict[str, str] | None:
        """构造代理与 MCP 启动环境。

        不需要任何增量时返回 `None`，让子进程完整继承父环境；否则返回
        已合并父环境的完整映射。
        """
        if not self._proxy_base_url:
            env: dict[str, str] | None = None
        else:
            settings_env = (
                load_settings_env(self._settings_path) if self._settings_path else {}
            )
            env = dict(os.environ) | build_proxy_env(settings_env, self._proxy_base_url)
        # stdio MCP 继承启动环境；只有 resume 能在启动前预先写入原生会话 ID。
        # `CC_SESSION_ID` 仅兼容旧版 CC 身份环境变量。
        if self._mcp_config:
            env = dict(env) if env is not None else dict(os.environ)
            from trowel_py.memory.paths import resolve_memory_root
            env["TROWEL_SESSION_ID"] = self.session_id
            env["TROWEL_HOST_KIND"] = "cc"
            env["MEMORY_ROOT"] = str(resolve_memory_root())
            if self._cc_session_id:
                env["CC_SESSION_ID"] = self._cc_session_id
                env["TROWEL_NATIVE_SESSION_ID"] = self._cc_session_id
        if self.agent_mcp_enabled:
            env = dict(env) if env is not None else dict(os.environ)
            env["MCP_CONNECT_TIMEOUT_MS"] = "5000"
            env["MCP_TIMEOUT"] = "10000"
        return env

    async def _ensure_process(self) -> None:
        """复用存活进程，或启动新 CC 进程并更新运行标识。"""

        if self._proc is not None:
            if self._proc.returncode is None:
                return
            if not self._process_tree_has_exited(self._proc):
                await self._kill()
                if not self._process_tree_has_exited(self._proc):
                    raise RuntimeError(
                        "previous Claude Code process group still needs reconciliation"
                    )
        self._mark_process_resource_closed()
        resume: str | None = None
        if self._started and self._cc_session_id:
            resume = self._cc_session_id
        elif self._resume_from:
            resume = self._resume_from
        proc = await self._spawn(resume_from=resume)
        identity = (
            self._process_controller.inspect(proc.pid)
            if self._process_controller is not None
            else None
        )
        if self._process_controller is not None and identity is None:
            await self._force_process_exit(proc)
            raise RuntimeError(f"cannot identify spawned process {proc.pid}")
        self._process_generation += 1
        resource_id = f"cc-process:{self.session_id}:{self._process_generation}"
        if self._resource_registry is not None:
            try:
                self._resource_registry.register_process_group(
                    resource_id=resource_id,
                    owner_scope=OwnerScope.SESSION,
                    resource_kind="claude_code_process_group",
                    pid=proc.pid,
                    runtime="claude_code",
                    runtime_generation=self._process_generation,
                    agent_session_id=self.session_id,
                )
            except BaseException:
                await self._force_process_exit(proc)
                raise
            self._process_resource_id = resource_id
        self._process_identity = identity
        self._proc = proc
        self._started = True

    async def _kill(self) -> None:
        """强制终止仍存活的 CC 进程组，并确认资源账本终态。"""
        proc = self._proc
        if proc is None or self._process_tree_has_exited(proc):
            self._mark_process_resource_closed()
            return
        await self._signal_process(proc, "SIGKILL")
        exited = await self._wait_process_exit(proc, self._close_kill_s)
        if not exited:
            self._mark_process_resource_needs_reconcile(
                "Claude Code process group survived SIGKILL"
            )
            return
        self._mark_process_resource_closed()

    async def _force_process_exit(self, proc: Any) -> None:
        """在 spawn 后登记失败时强制结束尚未公开的进程。"""

        await self._signal_process(proc, "SIGKILL")
        await self._wait_process_exit(proc, self._close_kill_s)

    async def _close_process(self, *, active: bool) -> None:
        """关闭 stdin，并按 INT、TERM、KILL 顺序收敛独立进程组。

        Args:
            active: 关闭开始时是否仍有发送或后台 drain；为 True 时先发 SIGINT。

        Raises:
            RuntimeError: SIGKILL 后进程组仍未退出，无法提交 closed。
        """

        proc = self._proc
        if proc is None or self._process_tree_has_exited(proc):
            self._mark_process_resource_closed()
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (BrokenPipeError, ProcessLookupError, OSError):
                pass
        if active:
            await self._signal_process(proc, "SIGINT")
            if await self._wait_process_exit(proc, self._close_interrupt_s):
                self._mark_process_resource_closed()
                return
        if await self._wait_process_exit(proc, self._close_term_s):
            self._mark_process_resource_closed()
            return
        await self._signal_process(proc, "SIGTERM")
        if await self._wait_process_exit(proc, self._close_term_s):
            self._mark_process_resource_closed()
            return
        await self._signal_process(proc, "SIGKILL")
        if await self._wait_process_exit(proc, self._close_kill_s):
            self._mark_process_resource_closed()
            return
        self._mark_process_resource_needs_reconcile(
            "Claude Code process group survived SIGKILL"
        )
        raise RuntimeError("Claude Code process group did not exit after SIGKILL")

    async def _signal_process(self, proc: Any, signal_name: str) -> None:
        """优先向已核验进程组发送信号，兼容替身时只操作根进程。

        Args:
            proc: 当前 CCHost 持有的子进程对象。
            signal_name: `SIGINT`、`SIGTERM` 或 `SIGKILL`。
        """

        if self._process_tree_has_exited(proc):
            return
        controller = self._process_controller
        identity = self._process_identity
        if controller is not None and identity is not None:
            self._signal_registered_process_group(signal_name)
            return
        self._signal_root_process(proc, signal_name)

    def _signal_root_process(self, proc: Any, signal_name: str) -> None:
        """无进程身份控制器时，只向当前根进程发送信号。

        Args:
            proc: 当前 CCHost 持有的子进程对象。
            signal_name: `SIGINT`、`SIGTERM` 或 `SIGKILL`。
        """

        try:
            if signal_name == "SIGINT":
                proc.send_signal(signal.SIGINT)
            elif signal_name == "SIGTERM":
                proc.terminate()
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    async def _wait_process_exit(self, proc: Any, timeout_s: float) -> bool:
        """有界等待根进程退出，并在可用时同时核验整个进程组。

        Args:
            proc: 当前 CCHost 持有的子进程对象。
            timeout_s: 最长等待秒数；0 表示只检查一次。
        """

        if proc.returncode is None and timeout_s > 0:
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                pass
        root_exited = proc.returncode is not None
        controller = self._process_controller
        identity = self._process_identity
        if controller is None or identity is None:
            return root_exited
        return root_exited and not controller.group_alive(identity.process_group)

    def _process_tree_has_exited(self, proc: Any) -> bool:
        """判断根进程与 spawn 时登记的独立进程组是否已经同时退出。"""

        if proc.returncode is None:
            return False
        controller = self._process_controller
        identity = self._process_identity
        return bool(
            controller is None
            or identity is None
            or not controller.group_alive(identity.process_group)
        )

    def _mark_process_resource_closed(self) -> None:
        """把当前 CC 进程资源提交为 closed，并清除本地资源 ID。"""

        resource_id = self._process_resource_id
        if resource_id is not None and self._resource_registry is not None:
            self._resource_registry.mark_closed(resource_id)
        self._process_resource_id = None
        self._process_identity = None

    def _mark_process_resource_needs_reconcile(self, error: str) -> None:
        """把无法确认退出的 CC 进程组保留为 needs_reconcile。

        Args:
            error: 不含正文或凭据的关闭失败说明。
        """

        resource_id = self._process_resource_id
        if resource_id is not None and self._resource_registry is not None:
            self._resource_registry.mark_needs_reconcile(resource_id, error)

    def _interrupt_proc(self, proc: Any) -> None:
        """向进程组发送 SIGINT，并容忍检查后退出的竞态。"""
        if self._process_controller is not None and self._process_identity is not None:
            self._signal_registered_process_group("SIGINT")
            return
        self._signal_root_process(proc, "SIGINT")

    def _sync_kill(self) -> None:
        """在 GeneratorExit 等无法 await 的路径中同步终止已核验进程组。"""
        proc = self._proc
        if proc is None or self._process_tree_has_exited(proc):
            self._mark_process_resource_closed()
            return
        if self._process_controller is not None and self._process_identity is not None:
            self._signal_registered_process_group("SIGKILL")
            return
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 — 清理不能遮蔽原始退出原因
            pass

    def _signal_registered_process_group(self, signal_name: str) -> None:
        """重查根 PID 启动身份后，才向登记的 CC 进程组发送信号。

        Args:
            signal_name: `SIGINT`、`SIGTERM` 或 `SIGKILL`。
        """

        controller = self._process_controller
        identity = self._process_identity
        if controller is None or identity is None:
            return
        current = controller.inspect(identity.pid)
        if current is not None and current != identity:
            self._mark_process_resource_needs_reconcile(
                "Claude Code process identity changed before signal"
            )
            return
        if current is None and not controller.group_alive(identity.process_group):
            return
        try:
            controller.signal_group(identity.process_group, signal_name)
        except ProcessLookupError:
            return
        except (PermissionError, OSError, RuntimeError) as exc:
            self._mark_process_resource_needs_reconcile(
                f"Claude Code process-group signal failed: {type(exc).__name__}"
            )

    async def interrupt(self) -> None:
        """中断当前 turn；已知原生会话 ID 时，后续发送会恢复上下文。"""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        self._interrupt_proc(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass

    async def close(self) -> None:
        """关闭后台 drain、workflow watcher 和会话子进程。

        host 拥有的 MCP 配置文件也会被删除。
        """
        active = self.has_in_flight_turn
        # drain 持有 stdout reader，终止进程前必须先取消它。
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):
                pass
            self._drain_task = None
        self._workflow_watcher.close()
        await self._close_process(active=active)
        if self._owned_mcp_config and self._mcp_config:
            Path(self._mcp_config).unlink(missing_ok=True)

    def discard_unstarted(self) -> None:
        """清理尚未启动的 host 及其自有 MCP 配置。

        Raises:
            RuntimeError: 会话已经启动，必须改走异步 close 流程。
        """

        if self._started or self._proc is not None or self._drain_task is not None:
            raise RuntimeError("started CC session cannot use create rollback")
        self._workflow_watcher.close()
        if self._owned_mcp_config and self._mcp_config:
            Path(self._mcp_config).unlink(missing_ok=True)

    async def reload(self) -> None:
        """结束当前 CC 子进程，使 revert 后的下一轮从截断的 JSONL 恢复。"""
        await self._kill()

    async def _prepare_checkpoint(self) -> tuple[str, bool]:
        """Git 工作区首轮复用启动 checkpoint，后续轮次在线程池中保存新快照。"""
        self._turn_count += 1
        if not checkpoint.is_enabled() or not checkpoint.is_git_repo(self.workdir):
            return uuid.uuid4().hex, False
        if self._turn_count == 1:
            return self._session_start_turn_id, True
        turn_id = uuid.uuid4().hex
        cc_sid = self._cc_session_id
        if not cc_sid:
            return turn_id, False
        jsonl_path = self._jsonl_path(cc_sid)
        offset = jsonl_path.stat().st_size if jsonl_path.is_file() else 0
        loop = asyncio.get_running_loop()
        revertible = await loop.run_in_executor(
            None,
            self._save_checkpoint_blocking,
            turn_id,
            str(jsonl_path),
            offset,
        )
        return turn_id, revertible

    async def _maybe_save_session_start_checkpoint(self, cc_sid: str) -> None:
        """原生会话 ID 就绪后，在线程池中幂等保存启动 checkpoint。"""
        if (
            self._session_start_saved
            or not checkpoint.is_enabled()
            or not checkpoint.is_git_repo(self.workdir)
        ):
            return
        jsonl_path = self._jsonl_path(cc_sid)
        offset = jsonl_path.stat().st_size if jsonl_path.is_file() else 0
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(
            None, self._save_session_start_blocking, str(jsonl_path), offset
        )
        if ok:
            self._session_start_saved = True

    async def _maybe_register_session(self, cc_sid: str) -> None:
        """在 executor 中等待注册完成，保持 init 事件的处理顺序。"""
        jsonl_path = self._jsonl_path(cc_sid)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._register_session_blocking, str(jsonl_path)
        )

    def _register_session_blocking(self, jsonl_path: str) -> None:
        """注册失败不能中断 CC 会话。"""
        if not self._cc_session_id:
            return
        try:
            session_registration.register_session(
                cc_session_id=self._cc_session_id,
                trowel_session_id=self.session_id,
                workdir=self.workdir,
                jsonl_path=jsonl_path,
                session_kind=self._session_kind,
                registrar=self._session_registrar,
            )
        except Exception as exc:  # noqa: BLE001 — 注册失败不能中断 CC 会话
            _wf_debug(f"memory session register failed (ignored): {exc}")

    async def _maybe_update_completed(self) -> None:
        """在线程池中更新当前 CC 会话已完整处理的 transcript 水位。"""
        if not self._cc_session_id:
            return
        jsonl_path = self._jsonl_path(self._cc_session_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._update_completed_blocking, str(jsonl_path)
        )

    def _update_completed_blocking(self, jsonl_path: str) -> None:
        """同步更新当前 CC 会话的 completed 水位；失败时不影响当前轮次。"""
        if not self._cc_session_id:
            return
        try:
            session_registration.update_completed(
                cc_session_id=self._cc_session_id,
                jsonl_path=jsonl_path,
                registrar=self._session_registrar,
            )
        except Exception as exc:  # noqa: BLE001 — 水位失败不能中断 CC turn
            _wf_debug(f"memory completed-offset update failed (ignored): {exc}")

    def _save_session_start_blocking(self, jsonl_path: str, offset: int) -> bool:
        """启用 checkpoint 时同步保存会话首轮前的文件快照。"""

        if not checkpoint.is_enabled():
            return False
        try:
            checkpoint.save(
                self.workdir,
                self._session_start_turn_id,
                cc_session_jsonl_path=jsonl_path,
                jsonl_offset=offset,
            )
            checkpoint.gc(self.workdir, keep=50)
        except (checkpoint.NotAGitRepoError, RuntimeError, OSError):
            return False
        return True

    def _save_checkpoint_blocking(
        self, turn_id: str, jsonl_path: str, offset: int
    ) -> bool:
        """启用 checkpoint 时同步保存指定轮次前的文件快照。"""

        if not checkpoint.is_enabled():
            return False
        try:
            checkpoint.save(
                self.workdir,
                turn_id,
                cc_session_jsonl_path=jsonl_path,
                jsonl_offset=offset,
            )
            checkpoint.gc(self.workdir, keep=50)
        except (checkpoint.NotAGitRepoError, RuntimeError, OSError):
            return False
        return True

    def _jsonl_path(self, cc_session_id: str) -> Path:
        """返回指定 CC 会话的主 transcript 路径。"""

        return (
            cc_projects_root()
            / workdir_to_slug(self.workdir)
            / f"{cc_session_id}.jsonl"
        )

    def _workflow_transcript_dir(self) -> Path | None:
        """返回 CC 2.1.197 存放 workflows 与 subagents 的会话目录。"""
        if not self._cc_session_id:
            return None
        return (
            cc_projects_root()
            / workdir_to_slug(self.workdir)
            / self._cc_session_id
        )

    def _update_bg_tracker(self, ev: dict[str, Any]) -> None:
        """同步后台任务状态，供实时读取与断线 drain 共用。"""
        if ev.get("type") != "system":
            return
        sub = ev.get("subtype")
        if sub == "task_started":
            self._bg_tracker.register_started(
                ev.get("task_id", ""),
                ev.get("tool_use_id", ""),
                ev.get("task_type"),
            )
        elif sub == "task_progress":
            self._bg_tracker.mark_progress(ev.get("task_id", ""))
        elif sub == "task_updated":
            patch = ev.get("patch")
            if isinstance(patch, dict) and patch.get("status") == "completed":
                # 真实录制中 TaskOutput(block=true) 可能只发 task_updated(completed)，
                # 不发顶层 task_notification；未知 task_id 仍由 terminate 忽略。
                self._bg_tracker.terminate(ev.get("task_id", ""))
        elif sub == "task_notification":
            self._bg_tracker.terminate(ev.get("task_id", ""))

    def _has_background_activity(self) -> bool:
        """判断逻辑 turn 是否仍有后台活动；此时 result 只是中途边界。"""
        return self._bg_tracker.has_pending_tasks() or (
            self._workflow_watcher.enabled
            and not self._workflow_watcher.all_done
        )

    async def send(self, text: str) -> AsyncIterator[TrowelEvent]:
        """处理会话输入；SendText 独占 stdout 直到逻辑 turn 结束。"""
        action = classify_input(text, self.workdir)
        # 每个子进程只能有一个 stdout reader；控制命令不写用户消息，因此不受此门禁。
        if self.running and isinstance(action, SendText):
            yield ErrorEvent(
                type="error",
                subclass="turn_in_progress",
                errors=["another turn is still running on this session"],
            )
            return

        if isinstance(action, LocalCommand):
            yield self._local_answer(action)
            return
        if isinstance(action, RestartSession):
            # 裸 /model 或 /effort 不应为无效切换终止现有进程。
            if not (action.model or action.effort):
                yield LocalCommandEvent(
                    type="local_command",
                    content=(
                        "用法：/model <别名> 或 /effort <级别>"
                        "（无参请在输入框的 picker 里选择）"
                    ),
                )
                return
            if action.effort:
                self.effort = action.effort
            if action.model:
                self._model = action.model
                self._effective_model = None
            await self._kill()
            stage = self._restart_stage(action)
            yield StatusEvent(type="status", stage=stage)
            # 进程下次 send 才重启，先发布新配置供界面立即同步。
            yield ModelChangedEvent(
                type="model_changed",
                model=self._model,
                effort=self.effort,
            )
            return
        if isinstance(action, UnsupportedSlash):
            yield LocalCommandEvent(type="local_command", content=action.message)
            return
        if isinstance(action, ExitSession):
            # stream-json 不识别字面量 /exit，必须改走 end_session 控制通道。
            async for tev in self._exit_session():
                yield tev
            return

        self.running = True
        payload = _user_msg(action.text)
        turn_id, revertible = await self._prepare_checkpoint()
        yield TurnStartEvent(
            type="turn_start", turn_id=turn_id, revertible=revertible
        )
        _wf_debug(
            f"SEND_START cc_sid={self._cc_session_id} text={action.text[:40]!r}"
        )
        translator = Translator()
        detector = StalledDetector(
            threshold_mild=self.stalled_threshold_mild,
            threshold_severe=self.stalled_threshold_severe,
            threshold_kill=self.stalled_threshold_kill,
        )
        detector.start_turn(self._now())
        # Workflow 可能在两轮之间结束，需强制重读未完成项。
        self._workflow_watcher.resync()
        mild_warned = False
        severe_warned = False

        normal_end = False
        cancelled = False
        try:
            # 先等旧 drain 释放 reader，再重置其 tracker，避免上一轮污染本轮。
            if self._drain_task is not None and not self._drain_task.done():
                try:
                    await self._drain_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._drain_task = None
            self._pending_terminal = None
            self._bg_tracker.reset()
            await self._ensure_process()
            if not await self._safe_write(payload):
                yield ErrorEvent(
                    type="error",
                    subclass="process_died",
                    errors=["CC process died before accepting input"],
                )
                normal_end = True
                return
            detector.record_event(self._now())

            while True:
                for wfev in self._workflow_watcher.poll():
                    yield wfev
                    # Workflow 有进展时，stdout 静默不代表 CC 卡死。
                    detector.record_event(self._now())
                proc = self._proc
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=self.stalled_tick
                    )
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        # 无 result 退出也必须为当前 turn 补一个终态。
                        yield ErrorEvent(
                            type="error",
                            subclass="host_error",
                            errors=["CC process exited without a result"],
                        )
                        break
                    # 真实 turn 边界是 result，不能用 all_done 后的静默代替。
                    # 等待用户 control_response 时静默是正常状态，不能触发 stall。
                    if self._pending_elicit is not None:
                        continue
                    # 后台任务或 Workflow 执行期间的静默也不计入 stall。
                    if self._has_background_activity():
                        detector.record_event(self._now())
                        continue
                    phase = detector.phase(self._now())
                    if phase == "kill":
                        elapsed = detector.quiet_seconds(self._now())
                        logger.warning(
                            "cc silent %.0fs, hard cap reached; killing + "
                            "surfacing stalled error",
                            elapsed,
                        )
                        yield ErrorEvent(
                            type="error",
                            subclass="stalled",
                            errors=[
                                f"CC silent {int(elapsed)}s — hard cap reached "
                                f"(possible stream-json deadlock; see issue #53584)"
                            ],
                        )
                        break
                    if phase == "severe":
                        if not severe_warned:
                            severe_warned = True
                            elapsed = detector.quiet_seconds(self._now())
                            logger.warning(
                                "cc silent %.0fs, severe heads-up", elapsed
                            )
                            yield StalledWarningEvent(
                                type="stalled_warning",
                                severity="severe",
                                elapsed_s=elapsed,
                            )
                        continue
                    if phase == "mild":
                        if not mild_warned:
                            mild_warned = True
                            elapsed = detector.quiet_seconds(self._now())
                            logger.warning(
                                "cc silent %.0fs, mild heads-up", elapsed
                            )
                            yield StalledWarningEvent(
                                type="stalled_warning",
                                severity="mild",
                                elapsed_s=elapsed,
                            )
                        continue
                    continue
                except ValueError as exc:
                    # 超长行无法安全 drain；明确结束 turn，避免 reader 死循环。
                    logger.warning(
                        "cc stream-json line exceeded StreamReader limit; "
                        "ending turn as overlong_line. error=%s",
                        exc,
                    )
                    yield ErrorEvent(
                        type="error",
                        subclass="overlong_line",
                        errors=[
                            "CC 输出的一行 stream-json 超过读取上限（16MB），"
                            "turn 已中止"
                        ],
                    )
                    normal_end = True
                    break
                if not raw:
                    # EOF 不是 turn 成功，仍需发布唯一的 host_error 终态。
                    yield ErrorEvent(
                        type="error",
                        subclass="host_error",
                        errors=["CC stdout closed without a result"],
                    )
                    break
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                detector.record_event(self._now())
                _et = ev.get("type")
                _es = str(ev.get("subtype", ""))
                if (
                    _et == "result"
                    or _et == "assistant"
                    or (_et == "system" and _es.startswith("task_"))
                ):
                    _wf_debug(
                        f"  EV ts={ev.get('timestamp','')} type={_et} sub={_es} "
                        f"tu={ev.get('tool_use_id','')}"
                    )
                if ev.get("type") == "system" and ev.get("subtype") == "api_retry":
                    delay = ev.get("retry_delay_ms")
                    if delay is not None:
                        detector.record_retry(self._now(), float(delay))
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    sid = ev.get("session_id")
                    effective_model = ev.get("model")
                    if isinstance(effective_model, str) and effective_model:
                        self._effective_model = effective_model
                    _wf_debug(f"INIT sid={sid}")
                    if sid:
                        self._cc_session_id = sid
                        await self._maybe_register_session(sid)
                        # init 时工作区尚未执行首轮，必须先补存启动 checkpoint。
                        if not self._session_start_saved:
                            await self._maybe_save_session_start_checkpoint(sid)
                        tdir = self._workflow_transcript_dir()
                        _wf_debug(f"  watcher set_transcript_dir={tdir}")
                        if tdir is not None:
                            self._workflow_watcher.set_transcript_dir(tdir)
                if ev.get("type") == "assistant":
                    message = ev.get("message")
                    effective_model = (
                        message.get("model") if isinstance(message, dict) else None
                    )
                    if isinstance(effective_model, str) and effective_model:
                        self._effective_model = effective_model
                self._update_bg_tracker(ev)
                for tev in translator.translate(ev):
                    if isinstance(tev, ElicitationRequestEvent):
                        self._pending_elicit = {
                            "request_id": tev.request_id,
                            "tool_use_id": tev.tool_use_id,
                            "questions": tev.questions,
                        }
                    if (
                        isinstance(tev, ToolCallEvent)
                        and tev.tool_name == "Workflow"
                    ):
                        _wf_debug(
                            f"  watcher enable (Workflow tu={tev.tool_use_id}) "
                            f"dir={self._workflow_transcript_dir()}"
                        )
                        self._workflow_watcher.enable()
                    if isinstance(tev, SubagentProgressEvent):
                        tev = self._backfill_subagent_usage(tev)
                    if isinstance(tev, SessionStartedEvent):
                        self._init_roster = list(tev.slash_commands)
                    # Translator 每个 result 只产出一个终态；先缓冲，再按后台状态决策。
                    if ev.get("type") == "result" and isinstance(
                        tev, (FinishedEvent, ErrorEvent)
                    ):
                        self._pending_terminal = tev
                        continue
                    yield tev
                if ev.get("type") == "result":
                    terminal = self._pending_terminal
                    is_error_result = not (
                        ev.get("subtype") == "success"
                        and not ev.get("is_error")
                    )
                    if is_error_result:
                        # 错误 result 始终结束本轮，但不算干净终态；finally 会杀掉
                        # 仍存活的进程，隔离可能迟到的后台事件。
                        self._bg_tracker.reset()
                        self._pending_terminal = None
                        if terminal is not None:
                            yield terminal
                        await self._maybe_update_completed()
                        break
                    if self._has_background_activity():
                        # 后台仍活动时，result 只是原生分段边界；丢弃成功终态，
                        # 继续收集自动续跑。Translator 已为下一原生片段重置累积器。
                        self._pending_terminal = None
                        # 前台段已结束但逻辑 turn 未完，避免静默期仍显示“生成中”。
                        yield StatusEvent(stage="background_waiting")
                        _wf_debug(
                            "  RESULT mid-turn (bg_pending="
                            f"{sorted(self._bg_tracker.pending_ids())}) — keep draining"
                        )
                    else:
                        normal_end = True
                        _wf_debug(
                            f"  RESULT terminal watching="
                            f"{self._workflow_watcher.is_watching} "
                            f"enabled={self._workflow_watcher.enabled}"
                        )
                        self._pending_terminal = None
                        if terminal is not None:
                            yield terminal
                            if isinstance(terminal, FinishedEvent):
                                self._last_finished = terminal
                        # 退出循环前推进 completed 水位，增量提炼只能读取完整 turn。
                        await self._maybe_update_completed()
                        break
            # 正常 result 后短暂确认进程状态：存活则复用，退出则通知前端。
            if normal_end and self._proc is not None:
                if self._proc.returncode is None:
                    try:
                        await asyncio.wait_for(self._proc.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                if self._proc.returncode is not None:
                    yield SessionExitedEvent(
                        type="session_exited",
                        returncode=self._proc.returncode,
                    )
        except asyncio.CancelledError:
            # SSE 断开只停止消费，不中止 CC；把 stdout 读取权移交给后台 drain。
            # GeneratorExit 等非取消退出仍由 finally 清理进程。
            cancelled = True
            # host 持有强引用；drain 自行清空，下一次 send 会等待它结束。
            self._drain_task = asyncio.create_task(
                self._drain_to_result_after_disconnect()
            )
            raise
        finally:
            self.running = False
            # 取消路径由 drain 接管；其他非干净退出必须同步杀进程。
            if not normal_end and not cancelled:
                self._sync_kill()

    async def answer_elicit(self, answers: dict[str, str]) -> bool:
        """向待处理 `AskUserQuestion` 写入 allow 回包。

        Args:
            answers: 按问题文本索引的答案，原样写入 `updatedInput.answers`。

        Returns:
            写入成功时返回 `True` 并清除待回答请求；无待回答请求或写入失败时
            返回 `False`，且保留待回答请求。
        """
        async with self._elicit_lock:
            pending = self._pending_elicit
            if pending is None:
                return False
            payload = _control_response_msg(
                request_id=pending["request_id"],
                behavior="allow",
                updated_input={
                    "questions": pending["questions"],
                    "answers": answers,
                    "annotations": {},
                },
            )
            # 先写后清；失败时保留 pending，允许重试和诊断。
            ok = await self._safe_write(payload)
            if ok:
                self._pending_elicit = None
            return ok

    async def cancel_elicit(self) -> bool:
        """向待处理 AskUserQuestion 写入 deny；无 pending 或写入失败时返回 False。"""
        async with self._elicit_lock:
            pending = self._pending_elicit
            if pending is None:
                return False
            payload = _control_response_msg(
                request_id=pending["request_id"],
                behavior="deny",
                message="User declined to answer questions",
            )
            ok = await self._safe_write(payload)
            if ok:
                self._pending_elicit = None
            return ok

    async def _safe_write(self, payload: bytes) -> bool:
        """写入 CC stdin，并将断管或 I/O 失败转为 `False`。

        Args:
            payload: 要写入的完整 stream-json 输入行。
        """
        try:
            await self._write(payload)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    async def _write(self, payload: bytes) -> None:
        """将一行 stream-json 写入当前 CC 进程并等待 stdin 排空。"""

        proc = self._proc
        proc.stdin.write(payload)
        await proc.stdin.drain()

    async def _drain_to_result_after_disconnect(self) -> None:
        """SSE 断开后不杀 CC，独占读取 stdout 直到逻辑 turn 结束。

        host 强持有本任务；下一次 send 等待它，close 会取消它。
        """
        self.running = True
        try:
            for _ in range(self._DRAIN_MAX_TICKS):
                proc = self._proc
                if proc is None or proc.returncode is not None:
                    return
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=self.stalled_tick
                    )
                except asyncio.TimeoutError:
                    continue
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                if not raw:
                    return
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # 与 live send 共用活动跟踪，保证逻辑终态判定一致。
                self._update_bg_tracker(ev)
                if ev.get("type") == "result":
                    if self._has_background_activity():
                        continue
                    # 用最终 result 刷新 /cost，并推进 completed 水位，
                    # 避免断线后的尾部事件遗留给下一轮。
                    if ev.get("subtype") == "success" and not ev.get("is_error"):
                        self._last_finished = FinishedEvent(
                            usage=ev.get("usage") or {},
                            total_cost_usd=float(ev.get("total_cost_usd", 0.0)),
                            num_turns=int(ev.get("num_turns", 0)),
                        )
                    else:
                        self._bg_tracker.reset()
                    # 水位失败不能终止 drain；后续正常终态还会再次推进。
                    try:
                        await self._maybe_update_completed()
                    except Exception as exc:  # noqa: BLE001 — 水位异常不能终止 drain
                        logger.warning(
                            "drain completed-offset update failed: %s", exc
                        )
                    return
        finally:
            self.running = False
            self._drain_task = None

    # end_session 最多等待三秒；超时后强杀，避免关闭流程卡住。
    _END_SESSION_TIMEOUT_S = 3.0
    # drain 由下一次 send 或 close 收口；默认配置下约一小时的上限只防真正挂死。
    _DRAIN_MAX_TICKS = 3600

    async def _exit_session(self) -> AsyncIterator[TrowelEvent]:
        """通过 stream-json 的 end_session 控制请求关闭 CC。

        启动、写入或超时失败也必须返回 SessionExitedEvent，避免前端卡住。

        Yields:
            一条携带最终退出码的 `SessionExitedEvent`；无法获取退出码时使用 0。
        """
        payload = (
            json.dumps(
                {
                    "type": "control_request",
                    "request": {"subtype": "end_session"},
                    "request_id": uuid.uuid4().hex,
                }
            )
            + "\n"
        ).encode()
        try:
            await self._ensure_process()
            wrote = await self._safe_write(payload)
            if not wrote:
                yield SessionExitedEvent(type="session_exited", returncode=0)
                return
        except Exception as exc:
            # 启动或写入失败也要结束 UI 会话，并记录原因。
            logger.warning("end_session control_request failed: %s", exc)
            yield SessionExitedEvent(type="session_exited", returncode=0)
            return

        deadline = time.monotonic() + self._END_SESSION_TIMEOUT_S
        while time.monotonic() < deadline:
            proc = self._proc
            if proc is None or proc.returncode is not None:
                break
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=min(self._END_SESSION_TIMEOUT_S, self.stalled_tick),
                )
            except asyncio.TimeoutError:
                continue
            if not raw:
                # EOF 后短暂等待 returncode 落定。
                if proc.returncode is None:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                break

        # 超过关闭窗口仍存活时强杀。
        if self._proc is not None and self._proc.returncode is None:
            await self._kill()
        rc = self._proc.returncode if self._proc is not None else 0
        yield SessionExitedEvent(type="session_exited", returncode=rc or 0)

    def _local_answer(self, action: LocalCommand) -> TrowelEvent:
        """根据最近 result 本地回答 `/cost` 或 `/status`，不访问 CC。"""
        if action.kind == "cost":
            f = self._last_finished
            if f is None:
                content = "no cost data yet"
            else:
                content = (
                    f"cost: ${f.total_cost_usd:.4f}  "
                    f"turns: {f.num_turns}  usage: {f.usage}  model: {self._model_for_display}"
                )
        else:
            state = "dead" if self.is_dead else "alive"
            content = (
                f"model: {self._model_for_display}  "
                f"effort: {self._effort_for_display}  process: {state}"
            )
        return LocalCommandEvent(type="local_command", content=content)

    def _restart_stage(self, action: RestartSession) -> str:
        """生成模型或 effort 切换后的状态文本。"""
        if action.effort:
            return f"restarting: effort={action.effort}"
        return f"restarting: model={action.model}"

    def _backfill_subagent_usage(
        self, tev: SubagentProgressEvent
    ) -> SubagentProgressEvent:
        """用子代理 transcript 汇总值覆盖 task_* 事件的 token 和工具调用用量。

        progress 与 completed 都需读取，以保持运行中和终态展示一致。

        Args:
            tev: 可能缺少 usage 的子代理进度事件。

        Returns:
            合并 transcript 用量后的新事件；无会话 ID、任务刚启动或无可用
            transcript 时返回原事件。
        """
        if not self._cc_session_id or not tev.task_id:
            return tev
        # started 发出时 transcript 尚未创建，避免必然失败的读取。
        if tev.status == "started":
            return tev
        path = subagent_transcript_path(
            self.workdir, self._cc_session_id, tev.task_id
        )
        summed = sum_transcript_usage(path)
        if summed is None:
            return tev
        return tev.model_copy(update={"usage": merge_usage(tev.usage, summed)})
