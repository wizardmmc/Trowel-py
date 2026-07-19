"""Transport behaviour tests against a scripted in-process app-server fake.

Each case maps to a clause in slice-070's pass criteria: handshake, concurrent
out-of-order responses, notification fan-out, server-request dispatch (with and
without a handler), bad JSON, unknown/duplicate ids, stderr, EOF, non-zero
exit, close idempotency, close timeout escalation and request timeouts.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from trowel_py.codex_host import AppServerClient
from trowel_py.codex_host.errors import (
    ProtocolViolationError,
    ServerRequestUnsupportedError,
    TransportClosedError,
    VersionMismatchError,
)
from trowel_py.codex_host.version import CodexVersion
from tests.codex_host._fake import Behavior, FakeAppServer, Step

# The transport pokes private state (``_state.pending``, ``_reader_task``) in
# a few assertions below — that is intentional: we are checking the kernel's
# own bookkeeping, not an external API.


async def _version_0144() -> CodexVersion:
    """Return the validated baseline version without spawning ``codex``."""

    return CodexVersion("codex-cli 0.144.0", (0, 144, 0))


def _build(
    behavior: Behavior,
    *,
    expected_version: str | None = "0.144.0",
    version_reader=_version_0144,
    **kwargs,
) -> tuple[AppServerClient, FakeAppServer]:
    """Wire an AppServerClient to a FakeAppServer."""

    fake = FakeAppServer(behavior)
    client = AppServerClient(
        codex_bin="codex",
        expected_version=expected_version,
        version_reader=version_reader,
        spawner=fake.spawner(),
        **kwargs,
    )
    return client, fake


def _initialize_response(request_id: object) -> Step:
    """Reply to ``initialize`` with the minimal result app-server returns."""

    return Step.send(
        {
            "id": request_id,
            "result": {
                "userAgent": "Codex Desktop/0.144.0 (<platform>) dumb (trowel_codex_host)",
                "codexHome": "<codex-home>",
                "platformFamily": "unix",
                "platformOs": "macos",
            },
        }
    )


def _handshake() -> Behavior:
    """A reusable initialize → response → initialized → idle script."""

    async def behavior():
        msg = yield Step.recv()
        assert msg is not None and msg["method"] == "initialize"
        yield _initialize_response(msg["id"])
        msg = yield Step.recv()
        assert msg is not None and msg["method"] == "initialized"
        yield Step.recv()  # idle until close

    return behavior()


# --------------------------------------------------------------- handshake


async def test_handshake_sends_initialize_then_initialized() -> None:
    """Spec §1: start() does initialize → wait → initialized."""

    async def behavior():
        msg = yield Step.recv()
        assert msg["method"] == "initialize"
        assert msg["params"]["clientInfo"]["name"] == "trowel_codex_host"
        yield _initialize_response(msg["id"])
        msg = yield Step.recv()
        assert msg["method"] == "initialized"
        yield Step.recv()

    client, fake = _build(behavior())
    await client.start()
    # ``initialized`` is a notification (no response) so the fake only records
    # it on the next event-loop tick — give it one before asserting.
    await asyncio.sleep(0.01)
    sent = [m["method"] for m in fake.received]
    assert sent == ["initialize", "initialized"]
    assert client.initialize_result is not None
    assert client.initialize_result["platformOs"] == "macos"
    await client.close()


async def test_handshake_rejects_unsupported_version() -> None:
    """Spec §1: a version outside the window blocks ready."""

    async def reader():
        return CodexVersion("codex-cli 0.200.0", (0, 200, 0))

    async def behavior():
        yield Step.recv()  # never reached — start raises first

    client, _fake = _build(behavior(), version_reader=reader)
    with pytest.raises(VersionMismatchError):
        await client.start()
    # Version check runs before the spawner — no process, no reader task.
    assert client._process is None
    assert client._reader_task is None


async def test_handshake_override_allows_unsupported_version(caplog) -> None:
    """Override proceeds but logs a warning — never silent (spec §1)."""

    async def reader():
        return CodexVersion("codex-cli 0.200.0", (0, 200, 0))

    client, _fake = _build(
        _handshake(), version_reader=reader, allow_version_override=True
    )
    with caplog.at_level("WARNING", logger="trowel_py.codex_host.version"):
        await client.start()
    assert any(
        "0.200.0" in r.message and "0.144.0" in r.message for r in caplog.records
    )
    await client.close()


# --------------------------------------------------------- request/response


async def test_normal_request_returns_result() -> None:
    """A simple request/response round-trips through the id-keyed future."""

    async def behavior():
        msg = yield Step.recv()  # initialize
        yield _initialize_response(msg["id"])
        yield Step.recv()  # initialized
        msg = yield Step.recv()  # thread/start
        yield Step.send({"id": msg["id"], "result": {"thread": {"id": "t-1"}}})
        yield Step.recv()

    client, fake = _build(behavior())
    await client.start()
    result = await client.request("thread/start", {"cwd": "/tmp"})
    assert result == {"thread": {"id": "t-1"}}
    assert fake.received[-1]["method"] == "thread/start"
    await client.close()


async def test_concurrent_100_out_of_order_responses() -> None:
    """Spec §2 + pass criteria: 100 concurrent, shuffled — no cross-talk."""

    count = 100

    async def behavior():
        msg = yield Step.recv()  # initialize
        yield _initialize_response(msg["id"])
        yield Step.recv()  # initialized
        pairs: list[tuple[object, int]] = []
        for _ in range(count):
            req = yield Step.recv()
            pairs.append((req["id"], int(req["params"]["i"])))
        random.shuffle(pairs)  # responses arrive out-of-order
        # Each response still carries its OWN request's i — ids must not cross.
        for request_id, i in pairs:
            yield Step.send({"id": request_id, "result": {"echo": i}})
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()

    async def call(i: int) -> int:
        return (await client.request("noop", {"i": i}))["echo"]

    results = await asyncio.gather(*(call(i) for i in range(count)))
    for i in range(count):
        assert results[i] == i  # every caller got its own i, never a neighbour's
    assert len(client._state.pending) == 0  # pending drained to zero
    await client.close()


async def test_error_response_raises_protocol_violation() -> None:
    """A JSON-RPC error object fails the request future."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send(
            {"id": msg["id"], "error": {"code": -32601, "message": "nope"}}
        )
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()
    with pytest.raises(ProtocolViolationError):
        await client.request("bogus/method", {})
    await client.close()


async def test_request_timeout_cleans_pending() -> None:
    """A timed-out request removes its future from the pending map."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.recv()  # the request that never gets a response
        yield Step.recv()  # idle until close

    client, _fake = _build(behavior())
    await client.start()
    with pytest.raises(asyncio.TimeoutError):
        await client.request("never/responds", {}, timeout=0.05)
    assert len(client._state.pending) == 0
    await client.close()


# ------------------------------------------------------------ notifications


async def test_notification_dispatches_to_listeners() -> None:
    """Spec §2: notifications fan out to registered listeners."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send({"method": "item/completed", "params": {"threadId": "t"}})
        yield Step.send({"method": "turn/completed", "params": {"turn": {"id": "u"}}})
        yield Step.recv()

    client, _fake = _build(behavior())
    seen: list[tuple[str, dict]] = []
    client.add_notification_listener(lambda m, p: seen.append((m, p)))
    await client.start()
    await asyncio.sleep(0.05)  # let the reader dispatch
    methods = [m for m, _ in seen]
    assert "item/completed" in methods
    assert "turn/completed" in methods
    await client.close()


# ----------------------------------------------------------- server requests


async def test_registered_server_request_handler_replies_result() -> None:
    """Spec §2: a registered handler answers; the server sees the decision."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 7,
                "params": {"command": "rm -rf /", "cwd": "/"},
            }
        )
        yield Step.recv()  # client's structured reply
        yield Step.recv()  # idle until close

    client, fake = _build(behavior())

    async def approve(_method: str, _params: dict) -> dict:
        return {"decision": "decline"}

    client.register_server_request_handler(
        "item/commandExecution/requestApproval", approve
    )
    await client.start()
    await asyncio.sleep(0.05)  # handler is async — give it a loop tick
    replies = [r for r in fake.received if r.get("id") == 7]
    assert len(replies) == 1
    assert replies[0]["result"] == {"decision": "decline"}
    await client.close()


async def test_unsupported_server_request_is_rejected_not_auto_approved() -> None:
    """Spec C-3: no handler → structured error response, never auto-accept."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 42,
                "params": {"command": "evil"},
            }
        )
        yield Step.recv()  # client's error reply
        yield Step.recv()  # idle

    client, fake = _build(behavior())
    # deliberately no handler registered
    await client.start()
    await asyncio.sleep(0.05)
    replies = [r for r in fake.received if r.get("id") == 42]
    assert len(replies) == 1
    assert "error" in replies[0]
    assert replies[0]["error"]["code"] == -32601
    assert "result" not in replies[0]  # never auto-approved
    await client.close()


async def test_handler_refusal_returns_one_error_no_fallthrough() -> None:
    """A handler raising ServerRequestUnsupportedError yields exactly one error
    reply — no fall-through to an unbound ``result`` success response.

    Regression guard: the refusal branch once omitted ``return``, which let
    control fall through to ``self._send({"result": result})`` with ``result``
    unbound → UnboundLocalError in the detached handler task.
    """

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 5,
                "params": {"command": "x"},
            }
        )
        yield Step.recv()  # the error reply
        yield Step.recv()  # idle

    client, fake = _build(behavior())

    async def refuse(_method: str, _params: dict) -> dict:
        raise ServerRequestUnsupportedError(
            "item/commandExecution/requestApproval", 5
        )

    client.register_server_request_handler(
        "item/commandExecution/requestApproval", refuse
    )
    await client.start()
    await asyncio.sleep(0.05)
    replies = [r for r in fake.received if r.get("id") == 5]
    assert len(replies) == 1  # exactly one reply — no duplicate success
    assert replies[0]["error"]["code"] == -32601
    assert "result" not in replies[0]
    await client.close()


async def test_default_expected_version_enforces_lock() -> None:
    """``AppServerClient()`` with no ``expected_version`` still rejects drift.

    Spec §1: the version lock is on by default, not opt-in.
    """

    async def reader() -> CodexVersion:
        return CodexVersion("codex-cli 0.999.0", (0, 999, 0))

    fake = FakeAppServer(_handshake())
    client = AppServerClient(version_reader=reader, spawner=fake.spawner())
    with pytest.raises(VersionMismatchError):
        await client.start()


async def test_env_overrides_merge_with_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial ``env`` never replaces PATH/HOME — it overlays them.

    Slice-080 will pass proxy overrides here; losing PATH would break the
    ``codex`` lookup (this exact bug bit the integration smoke once).
    """

    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()

    client, fake = _build(behavior(), env={"HTTPS_PROXY": "http://127.0.0.1:7897"})
    await client.start()
    assert fake.last_spawn_kwargs is not None
    child_env = fake.last_spawn_kwargs["env"]
    assert child_env["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert child_env["PATH"] == "/usr/local/bin:/usr/bin"  # parent PATH kept
    await client.close()


# --------------------------------------------------------- bad / odd inputs


async def test_bad_json_line_is_logged_not_fatal(caplog) -> None:
    """Spec §2: unparseable JSON records a diagnostic, transport survives."""

    async def behavior():
        msg = yield Step.recv()
        yield Step.send_raw("{not valid json")
        yield _initialize_response(msg["id"])
        yield Step.recv()

    client, _fake = _build(behavior())
    with caplog.at_level("WARNING", logger="trowel_py.codex_host.transport"):
        await client.start()
    assert any("valid JSON" in r.message for r in caplog.records)
    assert client.initialize_result is not None
    await client.close()


async def test_unknown_response_id_is_ignored(caplog) -> None:
    """Spec §2: a response for an id we never asked is diagnostic-only."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send({"id": "never-asked", "result": {}})
        yield Step.recv()

    client, _fake = _build(behavior())
    with caplog.at_level("WARNING", logger="trowel_py.codex_host.transport"):
        await client.start()
        await asyncio.sleep(0.05)
    assert any(
        "unknown" in r.message or "duplicate" in r.message for r in caplog.records
    )
    await client.close()


async def test_duplicate_response_id_ignored_after_first() -> None:
    """Spec §2: the second response for the same id is dropped."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        req = yield Step.recv()
        yield Step.send({"id": req["id"], "result": {"v": 1}})
        yield Step.send({"id": req["id"], "result": {"v": 2}})  # duplicate
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()
    result = await client.request("once/only", {})
    assert result == {"v": 1}  # first wins, duplicate ignored
    await client.close()


async def test_stderr_lines_captured_and_redacted() -> None:
    """stderr is drained; inline credentials are scrubbed in stderr_tail."""

    async def behavior():
        msg = yield Step.recv()
        yield Step.stderr("WARN schema cache stale")
        yield Step.stderr("token=sk-1234567890abcdef leaked")
        yield _initialize_response(msg["id"])
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()
    await asyncio.sleep(0.05)
    tail = client.stderr_tail
    assert "schema cache stale" in tail
    assert "sk-1234567890abcdef" not in tail
    assert "sk-***" in tail
    await client.close()


# -------------------------------------------------------- failure & close


async def test_eof_fails_pending_with_host_exited() -> None:
    """Spec C-5: EOF completes every pending future with TransportClosedError."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.exit(0)  # server vanishes

    client, _fake = _build(behavior())
    await client.start()

    async def hang() -> None:
        await client.request("hangs/forever", {}, timeout=5)

    task = asyncio.create_task(hang())
    await asyncio.sleep(0.05)
    with pytest.raises(TransportClosedError):
        await task
    assert client.closed
    await client.close()


async def test_nonzero_exit_records_exit_code() -> None:
    """Spec §3: a crash stamps the exit code on the failure."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.exit(1)

    client, _fake = _build(behavior())
    await client.start()

    async def hang() -> None:
        await client.request("hangs/forever", {}, timeout=5)

    task = asyncio.create_task(hang())
    await asyncio.sleep(0.05)
    with pytest.raises(TransportClosedError) as exc:
        await task
    assert exc.value.exit_code == 1
    await client.close()


async def test_close_is_idempotent() -> None:
    """Spec §3: close() twice does not raise and leaves no task behind."""

    client, _fake = _build(_handshake())
    await client.start()
    await client.close()
    await client.close()  # no error
    assert client._reader_task is None
    assert client._stderr_task is None
    assert client._process is None


async def test_close_escalates_when_process_ignores_stdin_close() -> None:
    """Spec §3: a process that won't exit is SIGTERM'd then SIGKILL'd."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        await (yield Step.hold(10))  # never exits voluntarily

    client, fake = _build(behavior(), close_grace_s=0.05, close_term_s=0.05)
    await client.start()
    await client.close()
    assert fake._process is not None
    assert fake._process.returncode in {-9, -15}  # had to be signaled


async def test_request_after_close_raises() -> None:
    """A request on a closed transport fails fast."""

    client, _fake = _build(_handshake())
    await client.start()
    await client.close()
    with pytest.raises(TransportClosedError):
        await client.request("anything", {})


async def test_no_orphan_tasks_after_close() -> None:
    """Pass criteria: no reader/writer/app-server task leaks after close."""

    client, _fake = _build(_handshake())
    await client.start()
    await asyncio.sleep(0.02)
    await client.close()
    leaked = [
        t
        for t in asyncio.all_tasks()
        if t.get_name()
        in {"codex-reader", "codex-stderr", "fake-app-server", "codex-server-request"}
    ]
    assert leaked == []


async def test_server_request_handler_task_is_cancelled_on_close() -> None:
    """A handler still awaiting its async work is cancelled by close (spec §3)."""

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 99,
                "params": {"command": "x"},
            }
        )
        yield Step.recv()  # idle until close

    client, _fake = _build(behavior())

    started = asyncio.Event()

    async def slow_handler(_method: str, _params: dict) -> dict:
        started.set()
        # Block forever — close() must cancel us.
        await asyncio.Event().wait()
        return {"decision": "accept"}

    client.register_server_request_handler(
        "item/commandExecution/requestApproval", slow_handler
    )
    await client.start()
    await started.wait()
    await client.close()
    handler_tasks = [
        t for t in asyncio.all_tasks() if t.get_name() == "codex-server-request"
    ]
    assert handler_tasks == []
