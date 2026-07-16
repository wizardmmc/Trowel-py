"""CCHost — long-lived CC subprocess manager.

One trowel session owns one long-lived `claude -p --input-format stream-json`
subprocess. send() feeds a user message and yields trowel events. Interrupt
(SIGINT) and a genuine stall both kill the process; the next send() lazily
respawns via `--resume <cc_session_id>`, preserving history.

Retry/fallback is left to CC (--fallback-model). The host only transparently
reports retries, restarts once on a stall per turn, and surfaces an error on
the second stall.
"""

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

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.input import (
    ExitSession,
    LocalCommand,
    RestartSession,
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
    TextEvent,
    ToolCallEvent,
    TrowelEvent,
    TurnStartEvent,
)
from trowel_py.memory.injection import build_memory_injection

logger = logging.getLogger(__name__)

# slice-036 TEMP DIAGNOSTIC: log the cc event timeline to /tmp to root-cause
# the "reply lags one turn" bug (problem 3/4). Records send boundaries + the
# `result` event's watcher state, so we can tell whether cc pushes `result`
# before a workflow finishes (=> post-result events land on the next turn).
# Remove once root-caused + fixed.
def _wf_debug(msg: str) -> None:
    """Diagnostic stub — slice-036 root-caused + fixed (see
    docs/design/front-end/cc-workflow-event-model.md). Real writes removed;
    calls remain as no-op trace points for future debugging.
    """
    pass




def _user_msg(text: str) -> bytes:
    """Encode a user text message in CC's stream-json input shape."""
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
    """Encode a control_response for CC's stream-json input (slice-025-c).

    Used to answer AskUserQuestion's control_request(can_use_tool):
    behavior=allow + updatedInput.{questions, answers, annotations} carries the
    user's selections back to cc (answers is a required record — 052 ZodError
    when absent). behavior=deny + message declines the question.

    Args:
        request_id: must match the control_request's request_id (cc blocks on it).
        behavior: "allow" or "deny".
        updated_input: the updatedInput record (required for allow, omitted for deny).
        message: deny reason (required for deny, omitted for allow).

    Returns:
        newline-terminated JSON bytes ready for cc stdin.
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
    """Default spawner: a real asyncio subprocess."""
    return await asyncio.create_subprocess_exec(*args, **kwargs)


class CCHost:
    """Owns one CC subprocess per session.

    The spawner/now hooks exist so tests can inject a fake process and a
    virtual clock instead of spawning real `claude`.
    """

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
    ) -> None:
        """Store session config and the injection hooks for tests.

        Args:
            session_id: trowel's id for this session (registry key).
            workdir: subprocess cwd for CC (loads that project's .claude/).
            model: override --model; None (default) omits the flag and CC reads
                ~/.claude/settings.json (slice-027: the old glm-5.2 was a placeholder).
            effort: override --effort; None (default) omits the flag (was medium).
            permission_mode: CC --permission-mode (default bypassPermissions).
            resume_from: optional CC session id to resume on first spawn.
            proxy_base_url: slice-030 local reverse proxy URL the CC subprocess
                targets as its ANTHROPIC_BASE_URL. None disables the proxy
                (pre-030 behavior / tests).
            settings_path: path to ~/.claude/settings.json, read for provider-var
                passthrough (token/model) when the proxy is on.
            spawner: async factory (args, kwargs) -> subprocess; defaults to a
                real asyncio subprocess, tests inject a fake.
            now: monotonic clock callable, injected so stalled tests are deterministic.
            stalled_threshold_mild: quiet seconds before the mild heads-up
                (StalledWarningEvent severity=mild). See StalledDetector.
            stalled_threshold_severe: quiet seconds before the severe heads-up.
            stalled_threshold_kill: quiet seconds before the hard cap (service
                kills cc + emits ErrorEvent). 30min default — genuine deadlock
                (issue #53584) backstop; normal GLM waits stay well under it.
            stalled_tick: how long to wait between stalled checks on readline.
            session_registrar: slice-040-b injectable registrar (duck-typed
                ``SessionRegistrar``). None (default) → the real
                ``~/.trowel/memory/meta/sessions.db`` is written via the lazy
                import path (unchanged for production + existing tests). Tests
                inject a no-op/capturing fake so they never touch the real db.
            session_kind: ``"user"`` (default) or ``"review"`` (the daily-review
                distillation sessions themselves). Stamped on the SessionRecord
                so ``find_pending(exclude_kinds=["review"])`` keeps the
                distillation sessions out of their own queue (C-5: kind, not
                workdir-path guessing).
        """
        self.session_id = session_id
        self.workdir = workdir
        self.running = False  # slice-028 D2: send() 期间 True（GET /sessions/active 用）
        # slice-028 v2: hold a strong ref to the background drain task so the
        # event loop doesn't GC it mid-run (Python's loop keeps only a weak ref
        # to tasks created via create_task). Cleared in the drain's finally.
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
        # slice-040-c: path to an mcp-config JSON attaching the memory MCP
        # server (search/read/outcome). None → no --mcp-config flag (pre-040-c).
        self._mcp_config = mcp_config

        self._proc: Any = None
        self._started = False
        self._cc_session_id: str | None = resume_from
        self._last_finished: FinishedEvent | None = None
        # slice-025-c: pending AskUserQuestion elicitation (set when translator
        # emits ElicitationRequestEvent, cleared on answer/cancel). None when
        # no interactive tool is awaiting the user. One at a time — cc does not
        # ask concurrently. The lock serializes answer/cancel so a double-submit
        # can't write two control_response rows for the same request_id.
        self._pending_elicit: dict[str, Any] | None = None
        self._elicit_lock = asyncio.Lock()
        # slice-042 P1: cc init's slash_commands roster — the authoritative
        # name floor for GET /slash-items (keeps up with cc updates). Filled
        # when SessionStartedEvent flows through send().
        self._init_roster: list[str] = []

        # slice-026 E1: a session-start checkpoint captures the worktree (and,
        # for resumed sessions, the jsonl cut) BEFORE turn 1 runs, so the first
        # turn is revertible too. _session_start_turn_id is the turn_id reused
        # by turn 1; _session_start_saved tracks whether the ref was written.
        self._session_start_turn_id = uuid.uuid4().hex
        self._session_start_saved = False
        self._turn_count = 0
        # slice-036: workflow progress watcher. cc runs Workflows in the
        # background and pushes NOTHING about them to stdout, so trowel reads
        # the on-disk wf_<runId>.json cc maintains. Lives across turns: a
        # workflow routinely outlives the turn that launched it (cc returns
        # once the Workflow is backgrounded), so its final state must surface
        # on a later send's poll (see resync + the poll at the top of the
        # readline loop in send()).
        self._workflow_watcher = WorkflowWatcher(self._workflow_transcript_dir())
        # Resumed session knows cc_session_id now → save session-start at once.
        # Fresh session defers to the init handler (cc_session_id unknown here).
        if checkpoint.is_git_repo(self.workdir) and resume_from:
            jsonl_path = self._jsonl_path(resume_from)
            offset = jsonl_path.stat().st_size if jsonl_path.is_file() else 0
            if self._save_session_start_blocking(str(jsonl_path), offset):
                self._session_start_saved = True

    # -- introspection -----------------------------------------------------

    @property
    def cc_session_id(self) -> str | None:
        """CC's session id (from system/init), used to --resume after a death."""
        return self._cc_session_id

    @property
    def model(self) -> str | None:
        """The --model currently in effect, or None when trowel is deferring to
        cc's ~/.claude/settings.json (slice-027 default)."""
        return self._model

    @property
    def _model_for_display(self) -> str:
        """Model name for /cost /status local text; None → '(cc default)'."""
        return self._model or "(cc default)"

    @property
    def _effort_for_display(self) -> str:
        """Effort for /cost /status local text; None → '(cc default)'."""
        return self.effort or "(cc default)"

    @property
    def is_dead(self) -> bool:
        """True when there is no live subprocess (none yet, or already exited)."""
        return self._proc is None or self._proc.returncode is not None

    # -- lifecycle ---------------------------------------------------------

    async def _spawn(self, resume_from: str | None) -> Any:
        """Build args/kwargs and spawn a CC subprocess via the configured spawner.

        Args:
            resume_from: CC session id to pass as --resume, or None for fresh.

        Returns:
            the spawned subprocess object (real or fake).
        """
        # slice-039: inject memory (layer-one + dictionary L0 + recent diary) via
        # cc's native --append-system-prompt. Failure degrades to "" — a memory
        # read error must NEVER block a cc spawn (spike 2026-07-09: append lands
        # in the system tail, proxy identity-rewrite stays untouched).
        try:
            injection = build_memory_injection(date.today().isoformat())
        except Exception:
            logger.warning(
                "memory injection failed; cc spawns without it", exc_info=True
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
        """Build the CC subprocess env that routes through the local reverse
        proxy (slice-030), or None to inherit the parent env (pre-030 behavior,
        tests, or when the proxy is disabled).

        Returns:
            A full env dict (os.environ merged with the proxy delta) when the
            proxy is on, else None so the subprocess inherits the parent env.
        """
        if not self._proxy_base_url:
            env: dict[str, str] | None = None
        else:
            settings_env = (
                load_settings_env(self._settings_path) if self._settings_path else {}
            )
            env = dict(os.environ) | build_proxy_env(settings_env, self._proxy_base_url)
        # slice-040-c: inject memory identity when the MCP server is attached.
        # The stdio MCP subprocess inherits cc's env (subprocessEnv 透传, reverse-
        # engineered), so TROWEL_SESSION_ID / MEMORY_ROOT / CC_SESSION_ID land in
        # the server. CC_SESSION_ID is None for a fresh session until init —
        # injected only when known (resume); per-call identity uses toolUseId.
        if self._mcp_config:
            env = dict(env) if env is not None else dict(os.environ)
            from trowel_py.memory.paths import resolve_memory_root
            env["TROWEL_SESSION_ID"] = self.session_id
            env["MEMORY_ROOT"] = str(resolve_memory_root())
            if self._cc_session_id:
                env["CC_SESSION_ID"] = self._cc_session_id
        return env

    async def _ensure_process(self) -> None:
        """Respawn if there is no live process. Resumes the known CC session."""
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
        """SIGKILL the subprocess if alive and await its exit (best-effort, 5s)."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass

    def _interrupt_proc(self, proc: Any) -> None:
        """Send SIGINT to the CC process group (overridable in tests).

        Tolerates the race where the process exits between the returncode check
        and getpgid/killpg — a missing process is not an error here.
        """
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _sync_kill(self) -> None:
        """Best-effort synchronous SIGKILL of the live process.

        Used in cancellation/cleanup paths where we cannot await (GeneratorExit)
        but must not leave an orphaned CC subprocess behind.
        """
        proc = self._proc
        if proc is None or getattr(proc, "returncode", None) is not None:
            return
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 — cleanup must never throw
            pass

    async def interrupt(self) -> None:
        """Cancel the current turn: SIGINT the process group. CC exits cleanly;
        the next send() will --resume."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        self._interrupt_proc(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass

    async def close(self) -> None:
        """End the session: cancel any background drain, then kill the subprocess."""
        # slice-028 v2: 取消后台 drain（前端 × 关闭 / DELETE），否则它持着 proc
        # 的 stdout reader，_kill 后还可能跑一阵。
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):
                pass
            self._drain_task = None
        # slice-036: drop workflow watcher tracking state (bounds memory).
        self._workflow_watcher.close()
        await self._kill()

    async def reload(self) -> None:
        """Kill the live CC process so the next send() re-resumes from disk.

        Used by the revert endpoint: after truncating the CC jsonl, the live
        subprocess still holds the pre-revert context in memory, so it must be
        respawned (``--resume`` then reloads the truncated jsonl) before the
        next turn. Idempotent — no-op when the process is already dead.
        """
        await self._kill()

    # -- checkpoint (slice-026 E1) -----------------------------------------

    async def _prepare_checkpoint(self) -> tuple[str, bool]:
        """Generate a turn_id and save a git checkpoint if possible.

        Returns:
            (turn_id, revertible). revertible is True only when a checkpoint
            is (or will be) written: the workdir is a git repo. Turn 1 reuses
            the session-start checkpoint (saved at construction for resumed
            sessions, or saved in the init handler for fresh ones); turns 2+
            get a fresh checkpoint saved here.

        The blocking git subprocess work runs in the default executor so the
        SSE event loop (and the stalled-detector heartbeat) keeps draining
        while the snapshot is taken (~100-300ms for a typical repo).
        """
        self._turn_count += 1
        if not checkpoint.is_git_repo(self.workdir):
            return uuid.uuid4().hex, False
        # turn 1: reuse the session-start checkpoint (no new save here).
        if self._turn_count == 1:
            return self._session_start_turn_id, True
        # turn 2+: fresh checkpoint at entry
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
        """Save the deferred session-start checkpoint for a fresh session.

        Called from the init handler once cc_session_id is learned. Idempotent
        (guarded by _session_start_saved). Runs git in the executor so the
        stream loop keeps draining.
        """
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
        """Register this cc session into the memory sessions db (slice-040).

        Runs the blocking sqlite insert on the default executor and awaits it:
        the executor keeps the event loop responsive to OTHER sessions, while
        awaiting preserves init ordering. The insert is sub-50ms, so the init
        handler is not meaningfully delayed. Never raises —
        ``_register_session_blocking`` swallows all errors, so a memory
        subsystem failure cannot break the cc session (same stance as memory
        injection in ``_spawn``). Idempotent via the sessions PK.
        """
        jsonl_path = self._jsonl_path(cc_sid)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._register_session_blocking, str(jsonl_path)
        )

    def _register_session_blocking(self, jsonl_path: str) -> None:
        """Blocking sqlite insert; runs in an executor. Swallows all errors.

        slice-040-b: when ``self._session_registrar`` is set (injected), the
        record is routed there and the real sessions.db is never touched — the
        basis of cc_host test isolation (C-2). The SessionRecord is built once
        with ``self._session_kind``; kind flows to whichever path is taken.
        """
        try:
            if not self._cc_session_id:
                # pre-init guard: nothing to register without a cc session id
                # (mirrors _maybe_update_completed; defends against an empty PK).
                return
            from datetime import datetime

            from trowel_py.memory.sessions_repo import SessionRecord

            now = datetime.now()  # one snapshot so date/registered_at can't drift
            rec = SessionRecord(
                cc_session_id=self._cc_session_id or "",
                workdir=str(self.workdir),
                date=now.date().isoformat(),
                jsonl_path=jsonl_path,
                registered_at=now.isoformat(),
                session_kind=self._session_kind,
            )
            if self._session_registrar is not None:
                self._session_registrar.register(rec)
                return
            from trowel_py.memory.paths import resolve_memory_root
            from trowel_py.memory.sessions_repo import (
                create_sessions_repository,
                open_sessions_db,
            )

            root = resolve_memory_root()
            conn = open_sessions_db(root)
            try:
                create_sessions_repository(conn).register(rec)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — never break the cc session
            _wf_debug(f"memory session register failed (ignored): {exc}")

    async def _maybe_update_completed(self) -> None:
        """Stamp the completed water mark at the result turn boundary (040-b C-6).

        Reads the jsonl byte size — every byte up to it is a fully-flushed turn,
        safe to distill — and forwards it to the registrar. Mirrors
        ``_maybe_register_session``: the blocking stat+update runs on the default
        executor and swallows all errors (a memory-subsystem failure must never
        break the cc turn). Called ONLY on the normal_end path; cancelled and
        stall-killed turns skip it — the next normal turn pushes the mark forward.

        Trade-off (CR H-2): the await runs on the result boundary every turn, so
        the event loop blocks for the sqlite open+update+close (~few ms). We
        accept this for a DETERMINISTICALLY landed water mark (the next
        incremental review reads it); a fire-and-forget variant would need
        task-lifecycle bookkeeping for marginal latency gain at this frequency.
        """
        if not self._cc_session_id:
            return
        jsonl_path = self._jsonl_path(self._cc_session_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._update_completed_blocking, str(jsonl_path)
        )

    def _update_completed_blocking(self, jsonl_path: str) -> None:
        """Blocking stat + update_completed; runs in an executor. Swallows errors."""
        try:
            if not self._cc_session_id:
                return  # pre-init guard: no water mark without a cc session id
            try:
                size = os.path.getsize(jsonl_path)
            except OSError:
                size = 0  # jsonl vanished (rotated) → treat as empty, not a crash
            if self._session_registrar is not None:
                self._session_registrar.update_completed(
                    self._cc_session_id or "", size
                )
                return
            from trowel_py.memory.paths import resolve_memory_root
            from trowel_py.memory.sessions_repo import (
                create_sessions_repository,
                open_sessions_db,
            )

            root = resolve_memory_root()
            conn = open_sessions_db(root)
            try:
                create_sessions_repository(conn).update_completed(
                    self._cc_session_id or "", size
                )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — never break the cc session
            _wf_debug(f"memory completed-offset update failed (ignored): {exc}")

    def _save_session_start_blocking(self, jsonl_path: str, offset: int) -> bool:
        """Blocking session-start save+gc; runs in an executor / sync __init__."""
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
        """Blocking save+gc for turns 2+; runs in an executor."""
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
        """Resolve the on-disk CC session jsonl for this workdir + cc sid."""
        return (
            cc_projects_root()
            / workdir_to_slug(self.workdir)
            / f"{cc_session_id}.jsonl"
        )

    def _workflow_transcript_dir(self) -> Path | None:
        """The session transcript dir that holds workflows/ + subagents/ (slice-036).

        cc 2.1.197 writes both ``<slug>/<cc_session_id>.jsonl`` (main dialogue)
        AND a same-named directory ``<slug>/<cc_session_id>/`` holding
        ``workflows/wf_<runId>.json`` + ``subagents/``. This returns the dir;
        None when cc_session_id isn't known yet (fresh session, pre-init).
        """
        if not self._cc_session_id:
            return None
        return (
            cc_projects_root()
            / workdir_to_slug(self.workdir)
            / self._cc_session_id
        )

    # -- send --------------------------------------------------------------

    async def send(self, text: str) -> AsyncIterator[TrowelEvent]:
        """Feed one user message and yield trowel events until the turn ends."""
        self.running = True  # slice-028 D2: 标记在跑（GET /sessions/active 用）
        action = classify_input(text, self.workdir)

        if isinstance(action, LocalCommand):
            yield self._local_answer(action)
            return
        if isinstance(action, RestartSession):
            # /model or /effort with no arg → classify_input returns
            # RestartSession(model=None, effort=None). The frontend picker is
            # supposed to intercept bare /model; if one still reaches here, do
            # NOT kill the live CC process for a no-op (would drop in-flight
            # context and confuse the user).
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
            # slice-027 C2: immediate sync so the StatusBar updates now. CC is
            # lazy-restarted by the next send's _ensure_process, so without
            # this the model/effort display would lag a full turn behind.
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
            # slice-028 bug3: /exit (alias /quit). CC's stream-json mode doesn't
            # intercept the literal string — shut down via the end_session
            # control_request channel, then surface SessionExitedEvent so the
            # frontend drops the multi-session row.
            async for tev in self._exit_session():
                yield tev
            return

        # SendText main path
        payload = _user_msg(action.text)
        # slice-026 E1: snapshot the worktree before the turn runs so the user
        # can revert it. turn_id names the checkpoint ref; revertible is False
        # for non-git workdirs and for a fresh session's first turn (no
        # resumable jsonl yet — cc_session_id is learned from init mid-turn).
        # Run in an executor — git subprocess calls would otherwise block the
        # SSE event loop (and the stalled-detector heartbeat) for ~100-300ms.
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
        # slice-036: a workflow often finishes between turns (cc backgrounds it
        # and returns). Re-read every non-finished runId this turn so the final
        # state surfaces even when its wf.json mtime hasn't moved since.
        self._workflow_watcher.resync()
        # Phased heads-up flags (per-turn): each severity fires once, then the
        # process is left alone until the next phase or the 30-min hard cap.
        mild_warned = False
        severe_warned = False

        normal_end = False
        cancelled = False  # slice-028 v2: CancelledError 时让后台 drain，finally 不杀
        try:
            # slice-028 v2: 如果上一轮 send 因前端断开走到了后台 drain，这里必须
            # 等它读完 stdout 到 result——否则这一轮 send 的 readline 会跟 drain 抢
            # 同一根管道，把上一轮 turn 的尾巴事件误算进这一轮（reducer 混乱）。
            # drain 跑完 = cc 已结束上一轮、写满 jsonl，本轮 send 干净开始。
            if self._drain_task is not None and not self._drain_task.done():
                try:
                    await self._drain_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._drain_task = None
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
                # slice-036: drain workflow snapshots every readline tick. Cheap
                # (stat only); a no-op until a Workflow tool_use enables the
                # watcher. Placed at loop top so it runs on every iteration —
                # after a readline timeout's `continue` rolls back here too.
                for wfev in self._workflow_watcher.poll():
                    yield wfev
                    # workflow activity = cc is alive; don't let the stalled
                    # detector count cc's background-workflow silence as a hang.
                    detector.record_event(self._now())
                # slice-036: turn boundary is cc's `result` event (see §5 of
                # docs/design/front-end/cc-workflow-event-model.md). We keep
                # draining past cc's early `result` when a workflow is in flight
                # (result handler below); the turn ends only on the final
                # `result`. The stalled detector (120s/300s/1800s) guards hangs.
                proc = self._proc
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=self.stalled_tick
                    )
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    # slice-036 fix: turn boundary is cc's `result` event, NOT
                    # all_done+silence. cc finishes a workflow, then thinks
                    # (silent ~4s) and emits the completion text + result. The
                    # old `all_done + silent → break` ended the turn during that
                    # think gap, dropping cc's completion text + result (verified
                    # by recording real cc stdout — see
                    # docs/design/front-end/cc-workflow-event-model.md §5). The
                    # stalled detector (120s/300s/1800s) still guards true hangs.
                    # slice-025-c: while cc awaits the user's control_response
                    # (AskUserQuestion), the stream is silent by design — don't
                    # let the stall watchdog kill the process and force a retry
                    # (which would make cc re-emit the AskUserQuestion).
                    if self._pending_elicit is not None:
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
                    # slice-028 bug1: cc 单行 stream-json 超 StreamReader limit
                    # (即使 limit 提到 16MB，理论上更大的行仍会超)。drain 超长行
                    # 复杂且易死循环，改为明确报错 + 结束 turn，不让 ValueError 冒
                    # 泡到 routes.py 变 host_error。超 16MB 的单行极罕见（实测最大
                    # 1.08MB），break + 明确报错是可接受的兜底。
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
                    break
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                detector.record_event(self._now())
                # slice-036 DIAGNOSTIC: record key cc events to see turn
                # boundaries vs workflow/subagent completion ordering.
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
                        # slice-040: register into the memory sessions db so the
                        # daily review job can find this session by date.
                        # Fire-and-forget; never breaks the cc session.
                        await self._maybe_register_session(sid)
                        # slice-026: fresh session — now that we know
                        # cc_session_id, save the deferred session-start
                        # checkpoint (worktree is still pristine at init).
                        if not self._session_start_saved:
                            await self._maybe_save_session_start_checkpoint(sid)
                        # slice-036: now that cc_session_id is known, point the
                        # workflow watcher at this session's transcript dir.
                        tdir = self._workflow_transcript_dir()
                        _wf_debug(f"  watcher set_transcript_dir={tdir}")
                        if tdir is not None:
                            self._workflow_watcher.set_transcript_dir(tdir)
                # slice-036: workflow completion. cc pushes a system
                # task_notification when the workflow finishes, but实测 (see
                # docs/design/front-end/cc-workflow-event-model.md §3) shows
                # workflow scenarios emit 0 task_notification, and its summary
                # field is the task description ("Count files in directory"),
                # NOT cc's completion text. The completion text is a normal
                # assistant TextEvent after TaskOutput returns. So route
                # task_notification through translator's normal path (no
                # special TextEvent routing — the old routing was wrong).
                for tev in translator.translate(ev):
                    if isinstance(tev, ElicitationRequestEvent):
                        # Remember the pending elicitation so answer_elicit /
                        # cancel_elicit can write the matching control_response.
                        # NB: stall detection is short-circuited while this is
                        # set (see the _pending_elicit guard above), so a stall
                        # never fires during a pending elicit — no kind field
                        # is needed for stall diagnosis.
                        self._pending_elicit = {
                            "request_id": tev.request_id,
                            "tool_use_id": tev.tool_use_id,
                            "questions": tev.questions,
                        }
                    if (
                        isinstance(tev, ToolCallEvent)
                        and tev.tool_name == "Workflow"
                    ):
                        # slice-036: a Workflow was launched in the background.
                        # cc will write workflows/wf_<runId>.json — start
                        # stat-polling it (the poll at loop top does the rest).
                        _wf_debug(
                            f"  watcher enable (Workflow tu={tev.tool_use_id}) "
                            f"dir={self._workflow_transcript_dir()}"
                        )
                        self._workflow_watcher.enable()
                    if isinstance(tev, SubagentProgressEvent):
                        # slice-036 D 层: cc under GLM reports total_tokens=0
                        # in task_* events; sum the subagent transcript's
                        # message usage instead (mirrors cc's own TUI).
                        tev = self._backfill_subagent_usage(tev)
                    if isinstance(tev, SessionStartedEvent):
                        # slice-042 P1: cache cc init's slash_commands roster
                        # as the name floor for GET /slash-items. A fresh list
                        # copy — cc sends a new roster on every init.
                        self._init_roster = list(tev.slash_commands)
                    yield tev
                    if isinstance(tev, FinishedEvent):
                        self._last_finished = tev
                if ev.get("type") == "result":
                    if (
                        self._workflow_watcher.enabled
                        and not self._workflow_watcher.all_done
                    ):
                        # slice-036: cc backgrounds the workflow and pushes
                        # `result` before it finishes. Keep draining so the
                        # workflow's progress + completion + cc's summary land
                        # on THIS turn (not the next). The loop-top guard ends
                        # the turn once the workflow reaches a terminal state.
                        _wf_debug(
                            "  RESULT but workflow in flight (watching="
                            f"{self._workflow_watcher.is_watching}) — keep draining"
                        )
                    else:
                        normal_end = True
                        _wf_debug(
                            f"  RESULT break watching={self._workflow_watcher.is_watching} "
                            f"enabled={self._workflow_watcher.enabled}"
                        )
                        # slice-040-b: stamp the completed water mark BEFORE
                        # breaking (the result turn is fully done; the next
                        # increment distils up to this byte offset).
                        await self._maybe_update_completed()
                        break
            # slice-028 bug3: result 后检测 cc 是否退出（用户 /exit）。普通 turn
            # proc 不退（returncode None）；/exit 后 proc 退（returncode 0）。短
            # wait 确认（真实 cc 普通 turn 会 timeout；FakeProc.wait 立即返回）。
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
            # slice-028 v2 多 session: 客户端断开（前端刷新 / 切换会话）= 暂时不
            # 消费 events，**绝不杀 cc**。单 session 时代这里 _sync_kill 是为了
            # 不留孤儿，但多 session 下 cc 必须继续跑完 turn——否则刷新会中断 AI
            # 思考、最后那个 turn 的回复永远丢失（实测 jsonl 末尾只写了 user）。
            # 把"读到 result 为止"的活移交到后台 task，让 cc 跑完写满 jsonl；前端
            # reconcile 后 resume 即可看到完整对话。GeneratorExit（gen.close，真
            # 不用了）仍走 finally 的 _sync_kill 兜底。
            cancelled = True
            # 存强引用防 GC（Python event loop 只对 task 持弱引用）；drain 自己
            # 在 finally 里清 self._drain_task = None。下一次 send 入口会 await 它。
            self._drain_task = asyncio.create_task(
                self._drain_to_result_after_disconnect()
            )
            raise
        finally:
            # slice-028 D2: send 结束，不再在跑（后台 drain 自己管 running 标志）
            self.running = False
            # GeneratorExit (gen.close) or any non-local exit without a clean
            # result: don't leave a live subprocess behind. CancelledError 走后台
            # drain，不杀。
            if not normal_end and not cancelled:
                self._sync_kill()

    async def answer_elicit(self, answers: dict[str, str]) -> bool:
        """Reply to the pending AskUserQuestion with the user's selections.

        Writes control_response(behavior=allow, updatedInput={questions, answers,
        annotations}) to cc stdin, then clears the pending state. cc unblocks and
        continues the turn (the next readline in send() resumes).

        Args:
            answers: {questionText: answerStr}. Multi-select answers are
                comma-separated strings (spec/04 A.2).

        Returns:
            True if a control_response was written; False if no elicitation is
            pending (caller surfaces an error).
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
            # Write first; clear pending only on success so a failed write
            # (dead subprocess) leaves the state intact for retry / diagnosis.
            ok = await self._safe_write(payload)
            if ok:
                self._pending_elicit = None
            return ok

    async def cancel_elicit(self) -> bool:
        """Decline the pending AskUserQuestion (behavior=deny).

        Used when the user presses Esc / Cancel, or (future) when they send a
        new message instead of answering (boundary in slice-025-c).

        Returns:
            True if a deny control_response was written; False if nothing pending
            or the write failed (pending left intact for retry).
        """
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
        """Write payload; return False if the process is dead (broken pipe)."""
        try:
            await self._write(payload)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    async def _write(self, payload: bytes) -> None:
        """Write raw bytes to the CC subprocess stdin and flush.

        Args:
            payload: one stream-json line (a user message), utf-8 encoded.
        """
        proc = self._proc
        proc.stdin.write(payload)
        await proc.stdin.drain()

    async def _drain_to_result_after_disconnect(self) -> None:
        """slice-028 v2 多 session: 客户端断开后，后台把 cc stdout 读到 result/
        EOF，让 cc 自己跑完这个 turn（写满 jsonl），**绝不杀进程**。

        为什么需要这个：单 session 时代前端断开 = 不用了，直接杀 cc 没问题。但多
        session 下刷新前端是"暂时断开、待会 reconcile 回来"——如果杀 cc，AI 正在
        思考的 turn 会被中断，那一句的回复永久丢失（实测 jsonl 末尾只剩 user）。
        后台 drain 让 cc 跑完，前端 reconcile + resume 即可看到完整对话。

        生命周期：CancelledError 的 except 用 ``self._drain_task = create_task(...)``
        存强引用（防 GC），这里 finally 清空。下一次 send() 入口会 await 这个 task
        到完成（避免两条 readline 抢同一根 stdout），close()/DELETE 会 cancel 它。
        ``_DRAIN_MAX_TICKS`` 只作 1 小时兜底（真 stalled 的 cc 交给下一次 send 的
        stalled 检测或 close 杀掉）。
        """
        self.running = True  # drain 期间 cc 还在跑（GET /sessions/active 准确）
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
                    return  # EOF — cc 退出
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "result":
                    return  # turn 跑完
        finally:
            self.running = False
            self._drain_task = None  # 释放强引用

    # slice-028 bug3: how long to wait for CC to flush + exit after we send the
    # end_session control_request before SIGKILL-ing it. CC is supposed to exit
    # promptly (gracefulShutdown has its own ~1.5s budget); 3s is a generous cap.
    _END_SESSION_TIMEOUT_S = 3.0
    # slice-028 v2: background-drain backstop. Primary lifecycle is explicit
    # (next send() awaits the drain; close()/DELETE cancels it). This cap only
    # catches a truly hung cc so the drain task can't run forever — 1 hour is
    # well past any real turn, so hitting it means cc is stalled.
    _DRAIN_MAX_TICKS = 3600

    async def _exit_session(self) -> AsyncIterator[TrowelEvent]:
        """slice-028 bug3: gracefully shut CC down via stream-json's
        control_request(subtype=end_session) channel.

        CC's stream-json mode does NOT intercept the literal "/exit" string (a
        `local-jsx` command in the interactive TUI only). The canonical
        non-interactive shutdown (cc's cli/print.ts) is to send a control_request
        with subtype=end_session: CC emits its final `result` event and exits 0.
        We write that request, drain stdout until the process dies, then yield
        SessionExitedEvent so the frontend drops the multi-session row.

        Fallback: if CC doesn't die within _END_SESSION_TIMEOUT_S (rare), we
        SIGKILL it and still yield exited so the UI is never stuck.
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
                # process already dead — nothing to drain
                yield SessionExitedEvent(type="session_exited", returncode=0)
                return
        except Exception as exc:
            # process never started or died writing — surface exited regardless
            # (UI 不该卡死)，但留日志便于排查（cc 命令不存在 / 权限 / 等）
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
                # EOF on stdout — process is exiting (or has exited). Confirm.
                if proc.returncode is None:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                break

        # if still alive after the drain window, force-kill
        if self._proc is not None and self._proc.returncode is None:
            await self._kill()
        rc = self._proc.returncode if self._proc is not None else 0
        yield SessionExitedEvent(type="session_exited", returncode=rc or 0)

    def _local_answer(self, action: LocalCommand) -> TrowelEvent:
        """Answer /cost or /status from accumulated result data (no CC call)."""
        if action.kind == "cost":
            f = self._last_finished
            if f is None:
                content = "no cost data yet"
            else:
                content = (
                    f"cost: ${f.total_cost_usd:.4f}  "
                    f"turns: {f.num_turns}  usage: {f.usage}  model: {self._model_for_display}"
                )
        else:  # status
            state = "dead" if self.is_dead else "alive"
            content = (
                f"model: {self._model_for_display}  "
                f"effort: {self._effort_for_display}  process: {state}"
            )
        return LocalCommandEvent(type="local_command", content=content)

    def _restart_stage(self, action: RestartSession) -> str:
        """Build the human-readable status string for a seamless restart.

        Args:
            action: the RestartSession carrying the new effort or model.

        Returns:
            a status string saying what changed (e.g. 'restarting: effort=high').
        """
        if action.effort:
            return f"restarting: effort={action.effort}"
        return f"restarting: model={action.model}"

    def _backfill_subagent_usage(
        self, tev: SubagentProgressEvent
    ) -> SubagentProgressEvent:
        """slice-036 D 层: replace cc's empty usage with a transcript sum.

        Under GLM, cc's task_progress/task_notification events carry
        ``total_tokens: 0`` (the field is empty there), so the SubagentBlock
        rendered "0 tokens" and hid the spend line. cc's own TUI shows real
        numbers because it sums each assistant message's usage from the
        subagent transcript. This reads that transcript and does the same.

        Reads at every SubagentProgressEvent (started/progress/completed): the
        transcript is small and task_* events are infrequent, so the IO cost is
        negligible, and reading on `completed` alone would miss the running
        progress display. Returns the original event unchanged when there is no
        transcript yet (subagent still booting / pre-init).

        Args:
            tev: the SubagentProgressEvent to backfill.

        Returns:
            A copy with ``usage`` replaced by :func:`merge_usage` output when a
            transcript exists; the original ``tev`` when no transcript is found.
        """
        if not self._cc_session_id or not tev.task_id:
            return tev
        # `started` arrives the moment cc forks the subagent — its transcript
        # doesn't exist yet, so the stat would always miss. Skip it (cc under
        # GLM only carries tokens on progress/completed anyway).
        if tev.status == "started":
            return tev
        path = subagent_transcript_path(
            self.workdir, self._cc_session_id, tev.task_id
        )
        summed = sum_transcript_usage(path)
        if summed is None:
            return tev
        return tev.model_copy(update={"usage": merge_usage(tev.usage, summed)})
