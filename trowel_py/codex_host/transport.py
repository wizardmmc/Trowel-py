"""通过异步 stdio 与 ``codex app-server`` 通信。

每个实例持有一个进程和一个 stdout reader；stdin 写入由锁串行化。客户端请求使用
UUID，和服务端主动请求的整数 ID 分域。未知服务端请求默认返回结构化拒绝。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
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
from trowel_py.codex_host.transport_state import _TransportState
from trowel_py.codex_host.version import CodexVersion, check_version, read_codex_version

_log = logging.getLogger(__name__)

JsonObject = dict[str, Any]
# handler 返回 JSON-RPC result；普通异常会转换为 error response。
ServerRequestHandler = Callable[[Any, str, JsonObject], Awaitable[JsonObject]]
# listener 在 reader task 内同步执行，必须非阻塞；单个 listener 异常不会杀死 reader。
NotificationListener = Callable[[str, JsonObject], None]
Spawner = Callable[[list[str], dict[str, Any]], Awaitable[Any]]

# 先关闭 stdin 并等待，再依次升级到 SIGTERM 和 SIGKILL。
_CLOSE_GRACE_S = 5.0
_CLOSE_TERM_S = 5.0

# app-server 的单行 JSON 可能包含大块工具结果或 diff；16 MiB 上限兼顾承载与内存边界。
_STDOUT_LIMIT_BYTES = 16 * 1024 * 1024

_ERR_METHOD_NOT_FOUND = -32601
_ERR_INTERNAL = -32603


class SubprocessLike(Protocol):
    stdin: Any
    stdout: Any
    stderr: Any

    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


class AppServerClient:
    """一条 ``codex app-server`` stdio 连接及其 JSON-RPC 状态。"""

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
        # 表示连接已在逻辑上关闭；主动 close 时可能早于进程完成回收。
        self._closed_event: asyncio.Event = asyncio.Event()
        # reader 在 EOF 时记录可见的进程退出码；进程尚未更新时仍可能是 None。
        self._last_exit_code: int | None = None
        self._server_handlers: dict[str, ServerRequestHandler] = {}
        self._unknown_server_handler: ServerRequestHandler | None = None
        self._server_request_tasks: set[asyncio.Task[None]] = set()
        self._notification_listeners: list[NotificationListener] = []
        self._stderr_lines: list[str] = []
        self._stderr_max = 200
        self._version: CodexVersion | None = None
        self._initialize_result: JsonObject | None = None
        recorder_path = (
            recorder_dir / "codex-appserver-protocol.jsonl" if recorder_dir else None
        )
        self._recorder = (
            RawRecorder(recorder_path) if recorder_path is not None else None
        )

    @property
    def version(self) -> CodexVersion | None:
        return self._version

    @property
    def initialize_result(self) -> JsonObject | None:
        return self._initialize_result

    @property
    def closed(self) -> bool:
        """连接开始关闭或 reader 已失效后为真。"""

        return self._state.closed

    async def wait_closed(self) -> None:
        """等待连接在逻辑上关闭；不保证此时子进程已经完成回收。"""

        await self._closed_event.wait()

    @property
    def last_exit_code(self) -> int | None:
        return self._last_exit_code

    @property
    def stderr_tail(self) -> str:
        """返回已在写入时脱敏的 stderr 尾部。"""

        return "\n".join(self._stderr_lines[-40:])

    async def start(self) -> JsonObject:
        """校验版本、启动进程并完成 initialize/initialized 握手。"""

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
            # 保留父环境中未显式覆盖的键；同名键以调用方传入值为准。
            kwargs["env"] = {**os.environ, **self._env}
        args = [self._codex_bin, *APP_SERVER_ARGS]
        self._process = await self._spawner(args, kwargs)
        self._reader_task = asyncio.create_task(self._read_loop(), name="codex-reader")
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
        """关闭连接并回收资源；重复调用不会再次执行关闭序列。

        顺序为：拒绝新请求、结束 pending、关闭 stdin、等待进程，再升级 TERM/KILL。
        """

        await self._shutdown(
            grace_s=timeout if timeout is not None else self._close_grace_s
        )

    async def _shutdown(self, *, grace_s: float) -> None:
        if not self._state.begin_closing():
            return
        # 所有 pending 必须收到同一个关闭原因，不能永久等待。
        host_error = TransportClosedError("app-server transport closed by client")
        await self._fail_all(host_error)
        proc = self._process
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001 — 关闭 stdin 仅作尽力清理
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
        """取消并等待 reader、stderr 与 server-request handler task。

        handler 由 ``_dispatch`` 独立创建，关闭时必须显式纳入回收。
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
        """以同一异常结束全部 pending，并将连接标为不可用。"""

        self._state.fail_all(error)
        # 主动关闭和 reader EOF 都经此处唤醒 wait_closed。
        self._closed_event.set()

    async def request(
        self, method: str, params: JsonObject | None = None, *, timeout: float = 60.0
    ) -> JsonObject:
        """发送 JSON-RPC 请求，并只接受相同 ID 的响应。"""

        if self.closed:
            raise TransportClosedError(
                "app-server transport is closed; cannot send request"
            )
        request_id = uuid.uuid4().hex
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._state.register(request_id, future)
        message: JsonObject = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self._send(message)
        except BaseException:
            self._state.discard(request_id)
            # fail-all 可能先结束 future；保留发送异常，同时领取内部关闭异常。
            if future.done() and not future.cancelled():
                future.exception()
            raise
        if future.done():
            # transport 可能在发送期间 fail_all，必须取出异常，避免 future 泄漏告警。
            exc = future.exception()
            if exc is not None:
                raise exc
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except BaseException:
            self._state.discard(request_id)
            raise
        return response

    async def notify(self, method: str, params: JsonObject | None) -> None:
        if self.closed:
            raise TransportClosedError(
                "app-server transport is closed; cannot send notification"
            )
        message: JsonObject = {"method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _send(self, message: JsonObject) -> None:
        """在 writer lock 内写一行 JSON；成功写出后才录制。"""

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

    def register_server_request_handler(
        self, method: str, handler: ServerRequestHandler
    ) -> None:
        self._server_handlers[method] = handler

    def register_unknown_server_request_handler(
        self, handler: ServerRequestHandler
    ) -> None:
        """注册未知请求的观察与拒绝入口。

        fallback 必须抛出 ``ServerRequestUnsupportedError``，由 transport 返回
        method-not-found；它只能先暴露安全拒绝事件，不能猜测成功结果。
        """

        self._unknown_server_handler = handler

    def add_notification_listener(self, listener: NotificationListener) -> None:
        self._notification_listeners.append(listener)

    async def _handle_server_request(self, message: JsonObject) -> None:
        """分发服务端请求，并使用原请求 ID 返回结果或错误。"""

        request_id = message["id"]
        method = str(message["method"])
        params = message.get("params")
        params_obj = params if isinstance(params, dict) else {}
        handler = self._server_handlers.get(method) or self._unknown_server_handler
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
            result = await handler(request_id, method, params_obj)
        except ServerRequestUnsupportedError as exc:
            _log.warning("Handler refused server request %s: %s", method, exc)
            await self._send_error(
                request_id, code=_ERR_METHOD_NOT_FOUND, message=str(exc)
            )
            return
        except Exception as exc:  # noqa: BLE001 — 将普通 handler 异常返回给服务端
            _log.exception("Server request handler %s raised", method)
            await self._send_error(
                request_id, code=_ERR_INTERNAL, message=f"{type(exc).__name__}: {exc}"
            )
            return
        await self._send({"id": request_id, "result": result})

    async def _send_error(self, request_id: Any, *, code: int, message: str) -> None:
        """尽力发送错误响应；只吞掉 transport 已关闭。"""

        try:
            await self._send(
                {"id": request_id, "error": {"code": code, "message": message}}
            )
        except TransportClosedError:
            _log.debug(
                "Could not reply to server request %r; transport closed", request_id
            )

    async def _read_loop(self) -> None:
        """持续读取并分发 JSONL；异常或 EOF 都会结束全部 pending。"""

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
        except Exception as exc:  # noqa: BLE001 — reader 异常必须留下诊断
            _log.exception("app-server reader crashed: %s", exc)
        finally:
            exit_code = proc.returncode
            self._last_exit_code = exit_code
            reason = self._host_exit_reason(exit_code)
            await self._fail_all(reason)

    def _dispatch(self, message: Any) -> None:
        """按消息类别分发；独立 handler task 会登记到关闭回收集合。"""

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
        """完成匹配的 pending future。

        活跃连接上的未知或重复 ID 会告警；关闭后迟到的响应属于预期竞态，静默丢弃。
        非对象 result 包装为 ``{"value": result}``，保持统一返回类型。
        """

        raw_id = message.get("id")
        request_id = str(raw_id) if raw_id is not None else ""
        future = self._state.pop(request_id)
        if future is None or future.done():
            if not self._state.closed:
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
        """同步调用 listener；隔离单个回调异常以保护 reader。"""

        method = str(message.get("method"))
        params = message.get("params")
        params_obj = params if isinstance(params, dict) else {}
        for listener in list(self._notification_listeners):
            try:
                listener(method, params_obj)
            except Exception:  # noqa: BLE001 — 单个 listener 不能中断 reader
                _log.exception("notification listener raised on %s", method)

    def _on_invalid_message(self, message: Any) -> None:
        _log.warning(
            "app-server sent a message that is not a valid JSON-RPC object: %s",
            self._safe_payload(message),
        )

    def _on_invalid_line(self, line: bytes, exc: Exception) -> None:
        """记录无法解析的 stdout 行，正文必须先脱敏并截断。"""

        text = line.decode("utf-8", errors="replace")[:200]
        _log.warning(
            "app-server stdout line was not valid JSON (%s): %s",
            exc,
            redact_stderr(text),
        )

    def _safe_payload(self, message: Any) -> Any:
        return redact_message(message)

    def _host_exit_reason(self, exit_code: int | None) -> TransportClosedError:
        """构造 EOF 时共享给全部 pending 的已脱敏错误。"""

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
        """持续排空 stderr，避免子进程阻塞，并写入有界脱敏缓冲。"""

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
        self._stderr_lines.append(redact_stderr(line))
        if len(self._stderr_lines) > self._stderr_max:
            del self._stderr_lines[: len(self._stderr_lines) - self._stderr_max]

    @staticmethod
    async def _default_spawner(args: list[str], kwargs: dict[str, Any]) -> Any:
        return await asyncio.create_subprocess_exec(*args, **kwargs)
