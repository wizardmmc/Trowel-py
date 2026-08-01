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
from trowel_py.resource_lifecycle.models import ProcessIdentity
from trowel_py.resource_lifecycle.processes import ProcessController

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
    """约束 transport 依赖的子进程最小接口，便于用测试进程替换 asyncio 进程。

    Attributes:
        stdin: 支持写入、等待写缓冲刷新、查询关闭状态和关闭操作的子进程标准输入。
        stdout: 支持异步逐行读取的子进程标准输出。
        stderr: 支持异步分块读取的子进程标准错误。
        returncode: 已知的退出码；进程尚未退出时为 None。
    """

    stdin: Any
    stdout: Any
    stderr: Any

    returncode: int | None
    pid: int

    def terminate(self) -> None:
        """请求子进程终止。"""

        ...

    def kill(self) -> None:
        """强制结束子进程。"""

        ...

    async def wait(self) -> int:
        """等待子进程退出并返回退出码。"""

        ...


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
        process_controller: ProcessController | None = None,
    ) -> None:
        """保存进程依赖与关闭策略，初始化一条尚未启动的连接。

        Args:
            codex_bin: 用于读取版本并启动 app-server 的 Codex 可执行文件。
            client_info: ``initialize`` 请求中的客户端身份；省略时使用 Trowel 默认值。
            expected_version: 已验证的 CLI 版本；None 表示跳过兼容性校验。
            allow_version_override: 版本不匹配时仅告警并继续启动。
            spawner: 接收命令参数和子进程选项的异步进程工厂。
            recorder_dir: 录制文件目录；启用 ``TROWEL_CODEX_RECORD`` 后，消息追加到
                该目录下的 ``codex-appserver-protocol.jsonl``。
            env: 覆盖到父环境之上的子进程环境变量。
            close_grace_s: 关闭 stdin 后等待进程自行退出的秒数。
            close_term_s: 发送 TERM 后等待进程退出、再升级 KILL 的秒数。
            version_reader: 替代 ``codex --version`` 的无参数异步版本读取器。
            process_controller: 核验并终止 app-server 独立进程组的实现；测试替身
                或旧调用方可省略，此时只操作根进程。
        """

        self._codex_bin = codex_bin
        self._client_info = client_info or ClientInfo()
        self._expected_version = expected_version
        self._allow_version_override = allow_version_override
        self._spawner = spawner or self._default_spawner
        self._env = env
        self._version_reader = version_reader
        self._process_controller = process_controller
        self._process_identity: ProcessIdentity | None = None
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
        """返回 ``start()`` 读取的 Codex 版本；尚未读取时为空。"""

        return self._version

    @property
    def initialize_result(self) -> JsonObject | None:
        """返回 app-server 对 ``initialize`` 请求的响应对象；尚未收到响应时为空。"""

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
        """返回 stdout reader 结束时读取到的进程退出码；状态未更新时为空。"""

        return self._last_exit_code

    @property
    def pid(self) -> int | None:
        """返回 app-server 根进程 PID；未启动或测试替身不提供时为空。"""

        process = self._process
        pid = getattr(process, "pid", None) if process is not None else None
        return pid if isinstance(pid, int) and pid > 0 else None

    @property
    def stderr_tail(self) -> str:
        """返回已在写入时脱敏的 stderr 尾部。"""

        return "\n".join(self._stderr_lines[-40:])

    async def start(self) -> JsonObject:
        """校验版本、启动进程并完成 ``initialize/initialized`` 握手。

        版本不兼容时不会创建进程。发送 ``initialize`` 请求失败时会先关闭连接，再
        原样上抛；发送 ``initialized`` 通知时的异常直接向调用方传播。

        Returns:
            app-server 对 ``initialize`` 请求返回的对象。
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
        if os.name == "posix":
            # app-server 及其 MCP 后代必须与 sidecar 分属不同进程组，才能按连接收敛。
            kwargs["start_new_session"] = True
        if self._env is not None:
            # 保留父环境中未显式覆盖的键；同名键以调用方传入值为准。
            kwargs["env"] = {**os.environ, **self._env}
        args = [self._codex_bin, *APP_SERVER_ARGS]
        self._process = await self._spawner(args, kwargs)
        if self._process_controller is not None and self.pid is not None:
            identity = self._process_controller.inspect(self.pid)
            if identity is None:
                self._process.kill()
                await self._process.wait()
                self._process = None
                raise RuntimeError("cannot identify Codex app-server process group")
            self._process_identity = identity
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

        Args:
            timeout: 关闭 stdin 后等待进程自行退出的秒数；省略时使用构造配置。
        """

        await self._shutdown(
            grace_s=timeout if timeout is not None else self._close_grace_s
        )

    async def _shutdown(self, *, grace_s: float) -> None:
        """只执行一次完整的连接关闭和进程回收。"""

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
            if not await self._wait_process_tree(proc, grace_s):
                await self._escalate(proc)
        await self._join_tasks()
        if self._recorder is not None:
            self._recorder.close()
        self._process = None
        self._process_identity = None

    async def _escalate(self, proc: SubprocessLike) -> None:
        """进程未自行退出时，依次尝试终止和强制结束。"""

        try:
            self._signal_process_tree(proc, "SIGTERM")
        except (ProcessLookupError, OSError):
            return
        if not await self._wait_process_tree(proc, self._close_term_s):
            try:
                self._signal_process_tree(proc, "SIGKILL")
            except (ProcessLookupError, OSError):
                return
            if not await self._wait_process_tree(proc, self._close_term_s):
                raise RuntimeError("Codex app-server process group survived SIGKILL")

    def _signal_process_tree(self, proc: SubprocessLike, signal_name: str) -> None:
        """优先向启动身份仍匹配的进程组发信号，否则只操作根进程。"""

        controller = self._process_controller
        identity = self._process_identity
        if controller is not None and identity is not None:
            current = controller.inspect(identity.pid)
            if current is not None and current != identity:
                raise RuntimeError("Codex app-server process identity changed")
            if current is None and not controller.group_alive(identity.process_group):
                return
            controller.signal_group(identity.process_group, signal_name)
            return
        if signal_name == "SIGTERM":
            proc.terminate()
        else:
            proc.kill()

    async def _wait_process_tree(
        self,
        proc: SubprocessLike,
        timeout_s: float,
    ) -> bool:
        """有界等待根进程和已登记进程组同时退出。"""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout_s, 0.0)
        if proc.returncode is None and timeout_s > 0:
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                return False
        if proc.returncode is None:
            return False
        controller = self._process_controller
        identity = self._process_identity
        if controller is None or identity is None:
            return True
        while controller.group_alive(identity.process_group):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.05, remaining))
        return True

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
        """以同一异常结束全部 pending，并唤醒连接关闭等待者。

        Args:
            error: 设置给每个未完成响应 Future 的连接失败原因。
        """

        self._state.fail_all(error)
        # 主动关闭和 reader EOF 都经此处唤醒 wait_closed。
        self._closed_event.set()

    async def request(
        self, method: str, params: JsonObject | None = None, *, timeout: float = 60.0
    ) -> JsonObject:
        """发送带 UUID 的 JSON-RPC 请求，并等待相同 ID 的响应。

        超时、取消或发送失败都会移除 pending 登记。JSON-RPC error 转为
        ``ProtocolViolationError``；非对象 result 包装在 ``{"value": result}`` 中。

        Args:
            method: app-server 的 JSON-RPC 方法名。
            params: 请求参数；None 表示不发送 ``params`` 字段。
            timeout: 等待响应的秒数。

        Returns:
            服务端返回的结果对象，或包装后的非对象结果。
        """

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
        """发送不含 ID、也不等待响应的 JSON-RPC 通知。

        Args:
            method: app-server 的 JSON-RPC 方法名。
            params: 通知参数；None 表示不发送 ``params`` 字段。
        """

        if self.closed:
            raise TransportClosedError(
                "app-server transport is closed; cannot send notification"
            )
        message: JsonObject = {"method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _send(self, message: JsonObject) -> None:
        """在 writer lock 内写一行 JSON，并在成功写出后录制。

        录制器异常发生在消息已发出之后，仍会向调用方传播。
        """

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
        """为一个服务端请求方法登记 handler；重复登记会替换旧 handler。

        Args:
            method: app-server 主动发起的 JSON-RPC 方法名。
            handler: 接收请求 ID、方法名和参数对象的异步处理函数。
        """

        self._server_handlers[method] = handler

    def register_unknown_server_request_handler(
        self, handler: ServerRequestHandler
    ) -> None:
        """注册未知请求的观察与拒绝入口。

        fallback 必须抛出 ``ServerRequestUnsupportedError``，由 transport 返回
        method-not-found；它只能先暴露安全拒绝事件，不能猜测成功结果。

        Args:
            handler: 接收未知请求并执行观察与拒绝逻辑的异步处理函数。
        """

        self._unknown_server_handler = handler

    def add_notification_listener(self, listener: NotificationListener) -> None:
        """追加一个在 reader task 内同步执行的通知监听器。

        listener 不得阻塞；其异常只记录日志，不影响后续 listener 或读取循环。

        Args:
            listener: 接收方法名和参数对象的同步回调。
        """

        self._notification_listeners.append(listener)

    async def _handle_server_request(self, message: JsonObject) -> None:
        """分发服务端请求，并使用原请求 ID 返回结果或错误。

        没有可用 handler 或 handler 主动拒绝时返回 method-not-found；其他异常返回
        internal-error。非对象 ``params`` 作为空对象传给 handler。
        """

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
        """持续读取并分发 JSONL；reader 异常或 EOF 会结束全部 pending。

        单行编码或 JSON 错误只记录诊断并跳过，不终止读取循环。
        """

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
        """记录格式不符合 JSON-RPC 的消息。"""

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
        """返回可安全写入诊断信息的脱敏消息。"""

        return redact_message(message)

    def _host_exit_reason(self, exit_code: int | None) -> TransportClosedError:
        """构造 reader 结束时共享给全部 pending 的已脱敏错误。"""

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
        """将一行脱敏 stderr 加入最多 200 行的诊断缓冲。"""

        self._stderr_lines.append(redact_stderr(line))
        if len(self._stderr_lines) > self._stderr_max:
            del self._stderr_lines[: len(self._stderr_lines) - self._stderr_max]

    @staticmethod
    async def _default_spawner(args: list[str], kwargs: dict[str, Any]) -> Any:
        """使用 asyncio 启动 Codex 子进程。"""

        return await asyncio.create_subprocess_exec(*args, **kwargs)
