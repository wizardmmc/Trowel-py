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

from trowel_py.schemas.cc_host import (
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

logger = logging.getLogger(__name__)

def _wf_debug(msg: str) -> None:
    """保留调用位作为诊断接缝，默认不产生 I/O。"""
    pass


def _user_msg(text: str) -> bytes:
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
    """编码 AskUserQuestion 回包；allow 时 `updatedInput` 必须包含 `answers`。"""
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
        mcp_config: str | None = None,
        memory_enabled: bool = True,
        profile_enabled: bool = True,
        self_enabled: bool = True,
    ) -> None:
        self.session_id = session_id
        self.workdir = workdir
        # routes 与 hub 会读取；send 或断线 drain 持有 stdout reader 时为 True。
        self.running = False
        # 事件循环只弱引用 task；必须持有 drain，直到它在 finally 中自行释放。
        self._drain_task: asyncio.Task | None = None
        self._model = model or DEFAULT_MODEL
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
        # 三个开关彼此独立，并在整个会话及重启期间保持不变。
        self._memory_enabled = memory_enabled
        self._profile_enabled = profile_enabled
        self._self_enabled = self_enabled
        # memory 关闭时在宿主边界丢弃配置，重启也不能重新接入 MCP 读路径。
        self._mcp_config = mcp_config if memory_enabled else None

        self._proc: Any = None
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
        if checkpoint.is_git_repo(self.workdir) and resume_from:
            jsonl_path = self._jsonl_path(resume_from)
            offset = jsonl_path.stat().st_size if jsonl_path.is_file() else 0
            if self._save_session_start_blocking(str(jsonl_path), offset):
                self._session_start_saved = True

    @property
    def cc_session_id(self) -> str | None:
        return self._cc_session_id

    @property
    def model(self) -> str | None:
        return self._model

    @property
    def _model_for_display(self) -> str:
        return self._model or "(cc default)"

    @property
    def _effort_for_display(self) -> str:
        return self.effort or "(cc default)"

    @property
    def is_dead(self) -> bool:
        return self._proc is None or self._proc.returncode is not None

    @property
    def memory_enabled(self) -> bool:
        return self._memory_enabled

    @property
    def profile_enabled(self) -> bool:
        return self._profile_enabled

    @property
    def self_enabled(self) -> bool:
        return self._self_enabled

    async def _spawn(self, resume_from: str | None) -> Any:
        # memory 读取失败只能降级注入，不能阻止 CC 启动。
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
        """返回代理与 MCP 环境；`None` 表示完整继承父进程环境。"""
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
        return env

    async def _ensure_process(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        resume: str | None = None
        if self._started and self._cc_session_id:
            resume = self._cc_session_id
        elif self._resume_from:
            resume = self._resume_from
        self._proc = await self._spawn(resume_from=resume)
        self._started = True

    async def _kill(self) -> None:
        """尽力终止仍存活的子进程，最多等待五秒。"""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass

    def _interrupt_proc(self, proc: Any) -> None:
        """向进程组发送 SIGINT，并容忍检查后退出的竞态。"""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _sync_kill(self) -> None:
        """在 GeneratorExit 等无法 await 的清理路径中同步终止子进程。"""
        proc = self._proc
        if proc is None or getattr(proc, "returncode", None) is not None:
            return
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 — 清理不能遮蔽原始退出原因
            pass

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
        """关闭后台 drain 和会话子进程。"""
        # drain 持有 stdout reader，终止进程前必须先取消它。
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):
                pass
            self._drain_task = None
        self._workflow_watcher.close()
        await self._kill()

    async def reload(self) -> None:
        """丢弃进程内旧上下文，使 revert 后的下一轮从截断 jsonl 恢复。"""
        await self._kill()

    async def _prepare_checkpoint(self) -> tuple[str, bool]:
        """首轮复用启动 checkpoint；后续轮次在线程池中保存新快照。"""
        self._turn_count += 1
        if not checkpoint.is_git_repo(self.workdir):
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
        if self._session_start_saved or not checkpoint.is_git_repo(self.workdir):
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
        """在逻辑终态等待 completed watermark 落地。"""
        if not self._cc_session_id:
            return
        jsonl_path = self._jsonl_path(self._cc_session_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._update_completed_blocking, str(jsonl_path)
        )

    def _update_completed_blocking(self, jsonl_path: str) -> None:
        """水位更新失败不能中断 CC turn。"""
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
        """向待处理 AskUserQuestion 写入 allow；成功后才清除 pending。"""
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
        """断管或写入失败时返回 False，不让异常逃出控制流程。"""
        try:
            await self._write(payload)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    async def _write(self, payload: bytes) -> None:
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
        """根据最近 result 本地回答 /cost 或 /status，不访问 CC。"""
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
        """用子代理 transcript 回填 task_* 的空 usage。

        progress 与 completed 都需读取，以保持运行中和终态展示一致。
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
