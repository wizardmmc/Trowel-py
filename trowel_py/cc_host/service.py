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
import os
import signal
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from trowel_py.cc_host.input import (
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
    build_args,
    build_subprocess_kwargs,
)
from trowel_py.cc_host.stalled import StalledDetector
from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import (
    ErrorEvent,
    FinishedEvent,
    LocalCommandEvent,
    SessionStartedEvent,
    StatusEvent,
    TrowelEvent,
)


def _user_msg(text: str) -> bytes:
    """Encode a user text message in CC's stream-json input shape."""
    payload = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
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
        resume_from: str | None = None,
        spawner: Callable[
            [list[str], dict[str, Any]], Awaitable[Any]
        ] = _default_spawner,
        now: Callable[[], float] = time.monotonic,
        stalled_threshold: float = 100.0,
        stalled_tick: float = 1.0,
    ) -> None:
        """Store session config and the injection hooks for tests.

        Args:
            session_id: trowel's id for this session (registry key).
            workdir: subprocess cwd for CC (loads that project's .claude/).
            model: override --model (defaults to glm-5.2 via launcher).
            effort: override --effort (defaults to medium).
            permission_mode: CC --permission-mode (default bypassPermissions).
            resume_from: optional CC session id to resume on first spawn.
            spawner: async factory (args, kwargs) -> subprocess; defaults to a
                real asyncio subprocess, tests inject a fake.
            now: monotonic clock callable, injected so stalled tests are deterministic.
            stalled_threshold: quiet seconds before a turn is considered stalled.
            stalled_tick: how long to wait between stalled checks on readline.
        """
        self.session_id = session_id
        self.workdir = workdir
        self._model = model or DEFAULT_MODEL
        self.effort = effort or DEFAULT_EFFORT
        self.permission_mode = permission_mode
        self._resume_from = resume_from
        self._spawner = spawner
        self._now = now
        self.stalled_threshold = stalled_threshold
        self.stalled_tick = stalled_tick

        self._proc: Any = None
        self._started = False
        self._cc_session_id: str | None = resume_from
        self._last_finished: FinishedEvent | None = None

    # -- introspection -----------------------------------------------------

    @property
    def cc_session_id(self) -> str | None:
        """CC's session id (from system/init), used to --resume after a death."""
        return self._cc_session_id

    @property
    def model(self) -> str:
        """The --model currently in effect."""
        return self._model

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
        args = build_args(
            self.workdir,
            model=self._model,
            effort=self.effort,
            permission_mode=self.permission_mode,
            resume_from=resume_from,
        )
        kwargs = build_subprocess_kwargs(self.workdir)
        return await self._spawner(args, kwargs)

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
        """End the session: kill the subprocess if still alive."""
        await self._kill()

    # -- send --------------------------------------------------------------

    async def send(self, text: str) -> AsyncIterator[TrowelEvent]:
        """Feed one user message and yield trowel events until the turn ends."""
        action = classify_input(text, self.workdir)

        if isinstance(action, LocalCommand):
            yield self._local_answer(action)
            return
        if isinstance(action, RestartSession):
            if action.effort:
                self.effort = action.effort
            if action.model:
                self._model = action.model
            await self._kill()
            stage = self._restart_stage(action)
            yield StatusEvent(type="status", stage=stage)
            return
        if isinstance(action, UnsupportedSlash):
            yield LocalCommandEvent(type="local_command", content=action.message)
            return

        # SendText main path
        payload = _user_msg(action.text)
        translator = Translator()
        detector = StalledDetector(threshold_s=self.stalled_threshold)
        detector.start_turn(self._now())

        normal_end = False
        try:
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
                proc = self._proc
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=self.stalled_tick
                    )
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    if detector.is_stalled(self._now()):
                        if detector.can_restart():
                            detector.note_restart()
                            await self._kill()
                            await self._ensure_process()
                            translator = Translator()
                            detector.record_event(self._now())
                            if not await self._safe_write(payload):
                                yield ErrorEvent(
                                    type="error",
                                    subclass="process_died",
                                    errors=["CC process died on retry resend"],
                                )
                                normal_end = True
                                return
                            continue
                        yield ErrorEvent(
                            type="error",
                            subclass="stalled",
                            errors=[
                                "CC stalled past threshold with no retry budget left"
                            ],
                        )
                        break
                    continue
                if not raw:
                    break
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                detector.record_event(self._now())
                if ev.get("type") == "system" and ev.get("subtype") == "api_retry":
                    delay = ev.get("retry_delay_ms")
                    if delay is not None:
                        detector.record_retry(self._now(), float(delay))
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    sid = ev.get("session_id")
                    if sid:
                        self._cc_session_id = sid
                for tev in translator.translate(ev):
                    yield tev
                    if isinstance(tev, FinishedEvent):
                        self._last_finished = tev
                if ev.get("type") == "result":
                    normal_end = True
                    break
        except asyncio.CancelledError:
            # client disconnected: kill CC so it isn't orphaned mid-turn
            self._sync_kill()
            raise
        finally:
            # GeneratorExit (gen.close) or any non-local exit without a clean
            # result: don't leave a live subprocess behind.
            if not normal_end:
                self._sync_kill()

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

    def _local_answer(self, action: LocalCommand) -> TrowelEvent:
        """Answer /cost or /status from accumulated result data (no CC call)."""
        if action.kind == "cost":
            f = self._last_finished
            if f is None:
                content = "no cost data yet"
            else:
                content = (
                    f"cost: ${f.total_cost_usd:.4f}  "
                    f"turns: {f.num_turns}  usage: {f.usage}  model: {self._model}"
                )
        else:  # status
            state = "dead" if self.is_dead else "alive"
            content = f"model: {self._model}  effort: {self.effort}  process: {state}"
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
