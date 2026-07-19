"""Async stdio client for ``codex app-server``.

One :class:`AppServerClient` owns one app-server process, exactly one stdout
reader task and one stdin writer (guarded by a lock). Pending request futures
are keyed by request id; notifications fan out to listeners; server-initiated
requests dispatch to registered handlers and default to a structured refusal.

The shape mirrors the synchronous client in ``codex/sdk/python`` (single reader
that classifies messages, ``fail_all`` on EOF, lock-guarded writer, stderr
drain) but is rebuilt on :mod:`asyncio` so it drops into trowel's FastAPI
backend. Client request ids are UUID strings so they can never collide with
the integer ids the server assigns to its own requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from trowel_py.codex_host.errors import (
    ProtocolViolationError,
    ServerRequestUnsupportedError,
    TransportClosedError,
)
from trowel_py.codex_host.protocol import (
    APP_SERVER_ARGS,
    SUPPORTED_CODEX_VERSION,
    ClientInfo,
    MessageKind,
    classify_server_message,
)
from trowel_py.codex_host.recorder import RawRecorder
from trowel_py.codex_host.secrets import redact_message, redact_stderr
from trowel_py.codex_host.version import CodexVersion, check_version, read_codex_version

_log = logging.getLogger(__name__)

JsonObject = dict[str, Any]
# A server-request handler returns the ``result`` object; raising any exception
# turns into a JSON-RPC error response with the handler's message.
ServerRequestHandler = Callable[[str, JsonObject], Awaitable[JsonObject]]
# A notification listener runs inline on the reader task. It MUST NOT block —
# any slow work must be offloaded to its own queue/task, otherwise it stalls
# every response and notification behind it. Exceptions are caught so a buggy
# listener cannot kill the reader.
NotificationListener = Callable[[str, JsonObject], None]
Spawner = Callable[[list[str], dict[str, Any]], Awaitable[Any]]

# Stdin is closed, then we wait this long for the process to exit on its own
# before escalating to SIGTERM and SIGKILL (spec §3 close semantics).
_CLOSE_GRACE_S = 5.0
_CLOSE_TERM_S = 5.0

# App-server speaks newline-delimited JSON and may put a large tool result,
# file body, or diff in one message. asyncio's 64 KiB subprocess default is
# too small for that wire format; keep a bounded 16 MiB ceiling, matching the
# already-spiked CC host transport.
_STDOUT_LIMIT_BYTES = 16 * 1024 * 1024

# JSON-RPC error codes reused for our own responses to the server.
_ERR_METHOD_NOT_FOUND = -32601
_ERR_INTERNAL = -32603


class SubprocessLike(Protocol):
    """The slice of :class:`asyncio.subprocess.Process` we depend on."""

    stdin: Any
    stdout: Any
    stderr: Any

    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


@dataclass
class _TransportState:
    """Mutable bookkeeping for one transport connection.

    Kept in a dataclass so :meth:`AppServerClient._fail_all` can swap a fresh
    empty state in under the lock, leaving the old pending map for drainage.

    Attributes:
        pending: request id → future awaiting its response.
        closing: True once close() has begun; new requests are refused.
        failed: True once the reader saw EOF / non-zero exit.
    """

    pending: dict[str, asyncio.Future[JsonObject]] = field(default_factory=dict)
    closing: bool = False
    failed: bool = False


class AppServerClient:
    """One stdio connection to ``codex app-server``.

    Lifecycle::

        async with AppServerClient() as client:   # spawns + handshakes
            result = await client.request("thread/start", {...})

    Or manual::

        client = AppServerClient()
        await client.start()
        try:
            ...
        finally:
            await client.close()

    The transport is safe to close twice and never leaves an app-server orphan
    or a dangling reader/writer task (spec §3).
    """

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        client_info: ClientInfo | None = None,
        expected_version: str | None = SUPPORTED_CODEX_VERSION,
        allow_version_override: bool = False,
        spawner: Spawner | None = None,
        recorder_dir: Path | None = None,
        env: dict[str, str] | None = None,
        close_grace_s: float = _CLOSE_GRACE_S,
        close_term_s: float = _CLOSE_TERM_S,
        version_reader: Callable[[], Awaitable[CodexVersion]] | None = None,
    ) -> None:
        """Store configuration; do not spawn yet.

        Args:
            codex_bin: Codex executable name or absolute path.
            client_info: Override the ``initialize.clientInfo`` block.
            expected_version: Pin a semver. Defaults to the supported baseline
                so a ``AppServerClient()`` with no arguments enforces the lock
                (spec §1: reject by default). Pass None only to skip — e.g.
                when the caller already validated the version out of band.
            allow_version_override: Log a warning instead of raising on version
                mismatch — development escape hatch (spec §1).
            spawner: Async factory ``(args, kwargs) -> process``. Tests inject
                a fake; production spawns a real asyncio subprocess.
            recorder_dir: Directory for the raw JSONL recording. Recording is
                env-gated and off by default (see :mod:`recorder`).
            env: Extra environment for the subprocess (e.g. proxy overrides).
                Merged on top of ``os.environ`` so the child keeps ``PATH`` /
                ``HOME`` — passing a partial dict never replaces the parent env.
                Never logged verbatim.
            close_grace_s: Seconds to wait for clean exit after stdin close.
            close_term_s: Seconds to wait after SIGTERM before SIGKILL.
            version_reader: Injectable ``codex --version`` reader. Production
                leaves it None (real read); tests return a fixed version so no
                real binary is spawned.
        """

        self._codex_bin = codex_bin
        self._client_info = client_info or ClientInfo()
        self._expected_version = expected_version
        self._allow_version_override = allow_version_override
        self._spawner = spawner or self._default_spawner
        self._env = env
        self._version_reader = version_reader
        self._close_grace_s = close_grace_s
        self._close_term_s = close_term_s
        self._process: SubprocessLike | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._writer_lock = asyncio.Lock()
        self._state = _TransportState()
        # Set once the transport closes — either the client called close() or
        # the reader observed EOF / non-zero exit. ``wait_closed`` awaits it so
        # a manager can react to host exit without polling (slice-071).
        self._closed_event: asyncio.Event = asyncio.Event()
        # Exit code the reader observed (None until the process is reaped).
        # ``close`` and a clean exit leave 0; a crash leaves non-zero. Surfaced
        # through ``last_exit_code`` so the manager can stamp HOST_EXITED with
        # a real exit code instead of always None (review M-2).
        self._last_exit_code: int | None = None
        self._server_handlers: dict[str, ServerRequestHandler] = {}
        self._server_request_tasks: set[asyncio.Task[None]] = set()
        self._notification_listeners: list[NotificationListener] = []
        self._stderr_lines: list[str] = []
        self._stderr_max = 200
        self._version: CodexVersion | None = None
        self._initialize_result: JsonObject | None = None
        recorder_path = (
            recorder_dir / "codex-appserver-protocol.jsonl" if recorder_dir else None
        )
        self._recorder = RawRecorder(recorder_path) if recorder_path is not None else None

    # ------------------------------------------------------------------ state

    @property
    def version(self) -> CodexVersion | None:
        """The installed Codex version read during :meth:`start`."""

        return self._version

    @property
    def initialize_result(self) -> JsonObject | None:
        """The ``initialize`` response result (``userAgent``, ``codexHome`` …)."""

        return self._initialize_result

    @property
    def closed(self) -> bool:
        """True once close() has run or the reader observed process exit."""

        return self._state.closing or self._state.failed

    async def wait_closed(self) -> None:
        """Block until the transport has closed.

        Returns when the client called :meth:`close` or the reader task
        observed EOF / a non-zero exit — whichever happens first. A manager
        awaits this to react to host exit without polling (slice-071).
        """

        await self._closed_event.wait()

    @property
    def last_exit_code(self) -> int | None:
        """The app-server exit code the reader observed, or None until reaped."""

        return self._last_exit_code

    @property
    def stderr_tail(self) -> str:
        """Return the tail of captured stderr.

        Lines are redacted on the way in (see :meth:`_append_stderr_line`), so
        no second pass is needed here.
        """

        return "\n".join(self._stderr_lines[-40:])

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> JsonObject:
        """Spawn the process, run ``initialize`` and send ``initialized``.

        Returns:
            The ``initialize`` response result object.

        Raises:
            VersionMismatchError: If the installed version differs from the
                pinned one and override is not allowed.
            TransportClosedError: If the process exits before handshake.
        """

        version = (
            await self._version_reader()
            if self._version_reader is not None
            else await read_codex_version(self._codex_bin)
        )
        self._version = version
        if self._expected_version is not None:
            check_version(
                version,
                supported=self._expected_version,
                allow_override=self._allow_version_override,
            )
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "limit": _STDOUT_LIMIT_BYTES,
        }
        if self._env is not None:
            # Merge on top of the parent env so the child keeps PATH / HOME
            # (passing a partial dict verbatim would break codex lookup and
            # CODEX_HOME discovery). Slice-080 will pass proxy overrides here.
            kwargs["env"] = {**os.environ, **self._env}
        args = [self._codex_bin, *APP_SERVER_ARGS]
        self._process = await self._spawner(args, kwargs)
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="codex-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name="codex-stderr"
        )
        try:
            result = await self.request(
                "initialize",
                {"clientInfo": self._client_info.as_dict()},
            )
        except BaseException:
            await self.close()
            raise
        self._initialize_result = result
        await self.notify("initialized", {})
        return result

    async def close(self, *, timeout: float | None = None) -> None:
        """Idempotently stop the transport and reap the process.

        Order (spec §3): refuse new requests → fail pending → close stdin →
        wait for exit → SIGTERM → SIGKILL. Safe to call after a failure.

        Args:
            timeout: Override the grace window before signal escalation.
        """

        await self._shutdown(
            grace_s=timeout if timeout is not None else self._close_grace_s
        )

    async def _shutdown(self, *, grace_s: float) -> None:
        """Run the close sequence once; subsequent calls are no-ops."""

        if self._state.closing:
            return
        self._state.closing = True
        # Fail every pending request with the same host-exited reason so no
        # caller waits forever (spec C-5).
        host_error = TransportClosedError("app-server transport closed by client")
        await self._fail_all(host_error)
        proc = self._process
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001 — best-effort close
                _log.debug("stdin close raised", exc_info=True)
        if proc is not None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace_s)
            except asyncio.TimeoutError:
                await self._escalate(proc)
        await self._join_tasks()
        if self._recorder is not None:
            self._recorder.close()
        self._process = None

    async def _escalate(self, proc: SubprocessLike) -> None:
        """SIGTERM then SIGKILL when the process ignores stdin close."""

        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._close_term_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                return
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass

    async def _join_tasks(self) -> None:
        """Cancel reader/stderr/handler tasks and await them.

        Server-request handler tasks are created detached in :meth:`_dispatch`;
        they must be cancelled here so :meth:`close` never leaves a handler
        running against a torn-down transport (spec §3 — no task leaks).
        """

        tasks = [
            t
            for t in (
                self._reader_task,
                self._stderr_task,
                *self._server_request_tasks,
            )
            if t is not None and not t.done()
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reader_task = None
        self._stderr_task = None
        self._server_request_tasks.clear()

    async def _fail_all(self, error: Exception) -> None:
        """Complete every pending future with ``error``; clear the map."""

        pending = self._state.pending
        self._state.pending = {}
        self._state.failed = True
        for future in pending.values():
            if not future.done():
                future.set_exception(error)
        # Notify anyone awaiting wait_closed() — covers both client-initiated
        # close (``_shutdown``) and server EOF (``_read_loop`` finally), since
        # both paths drain pending futures through here.
        self._closed_event.set()

    # ------------------------------------------------------------- requests

    async def request(
        self, method: str, params: JsonObject | None = None, *, timeout: float = 60.0
    ) -> JsonObject:
        """Send a JSON-RPC request and await its matching response.

        Args:
            method: App-server method name.
            params: Optional params object.
            timeout: Seconds to wait for the response.

        Returns:
            The response ``result`` object.

        Raises:
            TransportClosedError: If the transport is closing or failed.
            asyncio.TimeoutError: If no response arrives in time.
            ProtocolViolationError: If the server returns a JSON-RPC error.
        """

        if self.closed:
            raise TransportClosedError(
                "app-server transport is closed; cannot send request"
            )
        request_id = uuid.uuid4().hex
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._state.pending[request_id] = future
        message: JsonObject = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self._send(message)
        except BaseException:
            self._state.pending.pop(request_id, None)
            raise
        if future.done():
            # ``_fail_all`` ran during ``_send`` (transport closed mid-send).
            # Surface that exception instead of leaving it unretrieved.
            exc = future.exception()
            if exc is not None:
                raise exc
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except BaseException:
            self._state.pending.pop(request_id, None)
            raise
        return response

    async def notify(self, method: str, params: JsonObject | None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""

        if self.closed:
            raise TransportClosedError(
                "app-server transport is closed; cannot send notification"
            )
        message: JsonObject = {"method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _send(self, message: JsonObject) -> None:
        """Write one JSONL line under the writer lock and record it."""

        proc = self._process
        if proc is None or proc.stdin is None:
            raise TransportClosedError("app-server process is not running")
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._writer_lock:
            if proc.stdin.is_closing():
                raise TransportClosedError("app-server stdin is closing")
            proc.stdin.write(payload)
            await proc.stdin.drain()
        if self._recorder is not None:
            self._recorder.record("out", message)

    # -------------------------------------------------- server request bus

    def register_server_request_handler(
        self, method: str, handler: ServerRequestHandler
    ) -> None:
        """Register a handler for one server-initiated request method.

        Unknown methods are still rejected by default (spec C-3); this only
        teaches the transport how to answer a method it should support.
        """

        self._server_handlers[method] = handler

    def add_notification_listener(self, listener: NotificationListener) -> None:
        """Append a listener invoked synchronously for every notification."""

        self._notification_listeners.append(listener)

    async def _handle_server_request(self, message: JsonObject) -> None:
        """Dispatch one server request; always reply on the same id."""

        request_id = message["id"]
        method = str(message["method"])
        params = message.get("params")
        params_obj = params if isinstance(params, dict) else {}
        handler = self._server_handlers.get(method)
        if handler is None:
            _log.warning(
                "Rejecting unsupported server request %s (id=%r) — no handler registered",
                method,
                request_id,
            )
            await self._send_error(
                request_id,
                code=_ERR_METHOD_NOT_FOUND,
                message=f"No handler registered for {method}",
            )
            return
        try:
            result = await handler(method, params_obj)
        except ServerRequestUnsupportedError as exc:
            _log.warning("Handler refused server request %s: %s", method, exc)
            await self._send_error(request_id, code=_ERR_METHOD_NOT_FOUND, message=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — surface any handler failure
            _log.exception("Server request handler %s raised", method)
            await self._send_error(
                request_id, code=_ERR_INTERNAL, message=f"{type(exc).__name__}: {exc}"
            )
            return
        await self._send({"id": request_id, "result": result})

    async def _send_error(
        self, request_id: Any, *, code: int, message: str
    ) -> None:
        """Best-effort JSON-RPC error reply; transport-closed is logged."""

        try:
            await self._send(
                {"id": request_id, "error": {"code": code, "message": message}}
            )
        except TransportClosedError:
            _log.debug("Could not reply to server request %r; transport closed", request_id)

    # --------------------------------------------------------------- reader

    async def _read_loop(self) -> None:
        """Read stdout forever, classifying and dispatching each line."""

        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._on_invalid_line(line, exc)
                    continue
                if self._recorder is not None:
                    self._recorder.record("in", message)
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the reader must never die silently
            _log.exception("app-server reader crashed: %s", exc)
        finally:
            exit_code = proc.returncode
            self._last_exit_code = exit_code
            reason = self._host_exit_reason(exit_code)
            await self._fail_all(reason)
            self._state.failed = True

    def _dispatch(self, message: Any) -> None:
        """Route one parsed message to its handler based on classification."""

        kind = classify_server_message(message)
        if kind is MessageKind.RESPONSE:
            self._on_response(message)  # type: ignore[arg-type]
        elif kind is MessageKind.NOTIFICATION:
            self._on_notification(message)  # type: ignore[arg-type]
        elif kind is MessageKind.SERVER_REQUEST:
            task = asyncio.create_task(
                self._handle_server_request(message),  # type: ignore[arg-type]
                name="codex-server-request",
            )
            self._server_request_tasks.add(task)
            task.add_done_callback(self._server_request_tasks.discard)
        else:
            self._on_invalid_message(message)

    def _on_response(self, message: JsonObject) -> None:
        """Complete the matching pending future or log unknown/duplicate ids.

        A late response arriving after ``_fail_all`` emptied the pending map
        (transport closing/closed) is silently dropped — it is expected during
        shutdown, not a protocol anomaly worth warning about.
        """

        raw_id = message.get("id")
        request_id = str(raw_id) if raw_id is not None else ""
        future = self._state.pending.pop(request_id, None)
        if future is None or future.done():
            if not (self._state.closing or self._state.failed):
                _log.warning(
                    "app-server response for unknown or duplicate id %r — ignored",
                    raw_id,
                )
            return
        if "error" in message:
            err = message["error"]
            future.set_exception(
                ProtocolViolationError(
                    f"app-server returned error for {request_id}: {err}",
                    payload=self._safe_payload(message),
                )
            )
            return
        result = message.get("result")
        future.set_result(result if isinstance(result, dict) else {"value": result})

    def _on_notification(self, message: JsonObject) -> None:
        """Invoke every notification listener with method + params."""

        method = str(message.get("method"))
        params = message.get("params")
        params_obj = params if isinstance(params, dict) else {}
        for listener in list(self._notification_listeners):
            try:
                listener(method, params_obj)
            except Exception:  # noqa: BLE001 — a bad listener must not kill the reader
                _log.exception("notification listener raised on %s", method)

    def _on_invalid_message(self, message: Any) -> None:
        """Record a diagnostic for a message that failed classification."""

        _log.warning(
            "app-server sent a message that is not a valid JSON-RPC object: %s",
            self._safe_payload(message),
        )

    def _on_invalid_line(self, line: bytes, exc: Exception) -> None:
        """Record a diagnostic for an unparseable stdout line (redacted)."""

        text = line.decode("utf-8", errors="replace")[:200]
        _log.warning(
            "app-server stdout line was not valid JSON (%s): %s",
            exc,
            redact_stderr(text),
        )

    def _safe_payload(self, message: Any) -> Any:
        """Return a redacted copy of a message for diagnostic logging."""

        return redact_message(message)

    def _host_exit_reason(self, exit_code: int | None) -> TransportClosedError:
        """Build the exception every pending request fails with on EOF/exit."""

        if self._state.closing:
            return TransportClosedError(
                "app-server transport closed by client",
                exit_code=exit_code,
            )
        return TransportClosedError(
            f"app-server process exited (code={exit_code}); stderr={self.stderr_tail[:500]}",
            exit_code=exit_code,
        )

    async def _drain_stderr(self) -> None:
        """Continuously drain stderr into a bounded in-memory ring."""

        proc = self._process
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    self._append_stderr_line(line)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.debug("stderr drain ended", exc_info=True)

    def _append_stderr_line(self, line: str) -> None:
        """Append one (redacted) stderr line, evicting the oldest when full."""

        self._stderr_lines.append(redact_stderr(line))
        if len(self._stderr_lines) > self._stderr_max:
            del self._stderr_lines[: len(self._stderr_lines) - self._stderr_max]

    @staticmethod
    async def _default_spawner(args: list[str], kwargs: dict[str, Any]) -> Any:
        """Production spawner: a real asyncio subprocess."""

        return await asyncio.create_subprocess_exec(*args, **kwargs)
