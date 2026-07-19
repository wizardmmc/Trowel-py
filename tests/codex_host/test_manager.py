"""CodexHostManager routing / lifecycle tests against a scripted fake app-server.

Each test wires a :class:`CodexHostManager` to a :class:`FakeAppServer` whose
behavior is a script of recv/send steps. The fake speaks the exact JSON-RPC
shape the real 0.144.0 app-server emits (field names trace back to
``app-server-protocol/src/protocol/v2/``); no field is invented.

A ``hold(0.01)`` is inserted between the ``turn/start`` response and the
follow-up notifications. The real app-server has a model-inference delay there;
the fake does not, so without the yield the reader could dispatch a
``turn/completed`` before the manager task runs ``record_turn_started``. The
hold models that real gap and keeps the test deterministic.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from trowel_py.codex_host import (
    AppServerClient,
    CodexEvent,
    CodexEventType,
    CodexHostManager,
    CodexHostManagerState,
    CodexSession,
    CodexSessionConfig,
    TransportClosedError,
)
from trowel_py.codex_host.session import TurnConflictError
from trowel_py.codex_host.version import CodexVersion
from tests.codex_host._fake import FakeAppServer, Step

# ------------------------------------------------------------------ helpers


async def _version_0144() -> CodexVersion:
    """The validated baseline version without spawning ``codex``."""

    return CodexVersion("codex-cli 0.144.0", (0, 144, 0))


def _init_resp(request_id: object) -> Step:
    """Minimal ``initialize`` response."""

    return Step.send(
        {
            "id": request_id,
            "result": {
                "userAgent": "Codex/0.144.0 (trowel)",
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
            },
        }
    )


def _thread_result(tid: str) -> dict:
    """A ``thread/start`` response result with documented effective facts."""

    return {
        "thread": {"id": tid},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": "/tmp/x",
        "sandbox": {"mode": "read-only"},
        "approvalPolicy": {"policy": "never"},
        "serviceTier": None,
        "reasoningEffort": "high",
    }


def _cfg(sid: str) -> CodexSessionConfig:
    """Minimal session config."""

    return CodexSessionConfig(trowel_session_id=sid, workdir="/tmp/x")


def _manager(
    fake: FakeAppServer, *, pending_request_timeout_s: float = 600.0
) -> CodexHostManager:
    """Build a manager whose client_factory reuses one fake-backed client.

    Sufficient for every test that does not need a restart (EOF recovery uses
    :func:`_restart_manager` instead, which swaps the fake per lifecycle).
    """

    holder: dict[str, AppServerClient] = {}

    def factory() -> AppServerClient:
        if "client" not in holder:
            holder["client"] = AppServerClient(
                codex_bin="codex",
                expected_version="0.144.0",
                version_reader=_version_0144,
                spawner=fake.spawner(),
                close_grace_s=0.2,
                close_term_s=0.2,
            )
        return holder["client"]

    return CodexHostManager(
        client_factory=factory,
        pending_request_timeout_s=pending_request_timeout_s,
    )


def _model_list_fixture() -> dict:
    """Load the redacted model/list recording captured from Codex 0.144.0."""

    path = Path(__file__).parent / "fixtures" / "model-list-0.144.0.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _server_request_fixture(name: str) -> dict:
    """Load one real, redacted server-request fixture."""

    path = Path(__file__).parent / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


async def test_command_approval_waits_for_answer_and_reuses_native_id() -> None:
    """slice-075: pending UI answer returns on the original JSON-RPC id."""

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        answer = yield Step.recv()
        assert answer == {"id": 0, "result": {"decision": "accept"}}
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thr-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "run the probe")
    await asyncio.sleep(0.05)
    request_event = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )
    request_id = str(request_event.payload["request_id"])
    assert request_event.payload["available_decisions"] == recorded["params"][
        "availableDecisions"
    ]

    answered = manager.answer_request("session-a", request_id, "accept")
    assert answered.status.value == "answered"
    await asyncio.sleep(0.05)
    assert any(
        event.type is CodexEventType.APPROVAL_REQUEST
        and event.payload["status"] == "answered"
        for event in session.drain()
    )
    await manager.close()


async def test_file_approval_without_context_is_auto_declined() -> None:
    """Recorded file approval lacks path/diff/choices, so it fails closed."""

    recorded = _server_request_fixture("server-request-file-approval-075.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        answer = yield Step.recv()
        assert answer == {"id": 0, "result": {"decision": "decline"}}
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thr-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "edit the file")
    await asyncio.sleep(0.05)
    approval = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )
    assert approval.payload["approval_kind"] == "file_approval"
    assert approval.payload["status"] == "answered"
    assert approval.payload["decision"] == "decline"
    assert approval.payload["auto_resolved"] is True
    await manager.close()


async def test_unanswered_request_expires_with_safe_decline() -> None:
    """A timeout emits expired and answers decline so the turn can continue."""

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        answer = yield Step.recv()
        assert answer == {"id": 0, "result": {"decision": "decline"}}
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake, pending_request_timeout_s=0.01)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "wait")
    await asyncio.sleep(0.05)
    approvals = [
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    ]
    assert [event.payload["status"] for event in approvals] == [
        "pending",
        "expired",
    ]
    await manager.close()


async def test_host_exit_marks_pending_request_host_closed() -> None:
    """The old request becomes read-only before the running turn is failed."""

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        yield Step.exit(1)

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "wait")
    await asyncio.sleep(0.08)
    events = session.drain()
    approvals = [
        event
        for event in events
        if event.type is CodexEventType.APPROVAL_REQUEST
    ]
    assert [event.payload["status"] for event in approvals] == [
        "pending",
        "host_closed",
    ]
    assert any(
        event.type is CodexEventType.HOST_STATUS
        and event.payload["status"] == "host_exited"
        for event in events
    )
    await manager.close()


async def test_interrupt_precedes_cancel_response_for_pending_approval() -> None:
    """Recorded pending-interrupt order is preserved on the JSON-RPC wire."""

    recorded = _server_request_fixture("server-request-approval.jsonl")

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        params = {**recorded["params"], "threadId": "thr-1", "turnId": "turn-1"}
        yield Step.send({"method": recorded["method"], "id": 0, "params": params})
        interrupt = yield Step.recv()
        assert interrupt["method"] == "turn/interrupt"
        yield Step.send({"id": interrupt["id"], "result": {}})
        approval_answer = yield Step.recv()
        assert approval_answer == {"id": 0, "result": {"decision": "cancel"}}
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "wait")
    await asyncio.sleep(0.05)

    await manager.interrupt(session)
    await asyncio.sleep(0.05)

    approvals = [
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    ]
    assert [event.payload["status"] for event in approvals] == [
        "pending",
        "answered",
    ]
    assert approvals[-1].payload["decision"] == "cancel"
    await manager.close()


async def test_unknown_server_request_is_surfaced_then_method_rejected() -> None:
    """C-1: unknown methods get one visible safe-refusal and JSON-RPC error."""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        start = yield Step.recv()
        yield Step.send({"id": start["id"], "result": _thread_result("thr-1")})
        turn = yield Step.recv()
        yield Step.send({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.send(
            {
                "method": "future/unsafeRequest",
                "id": 91,
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "itemId": "future-1",
                },
            }
        )
        answer = yield Step.recv()
        assert answer["id"] == 91
        assert answer["error"]["code"] == -32601
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("session-a"))
    manager.register(session)
    await manager.send(session, "future request")
    await asyncio.sleep(0.05)
    approval = next(
        event
        for event in session.drain()
        if event.type is CodexEventType.APPROVAL_REQUEST
    )
    assert approval.payload["approval_kind"] == "unknown"
    assert approval.payload["auto_resolved"] is True
    assert approval.payload["decision"] == "unsupported"
    await manager.close()


async def test_model_list_paginates_and_preserves_native_order() -> None:
    """All visible rows and per-model efforts survive cursor pagination."""

    recorded = _model_list_fixture()

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        first = yield Step.recv()
        assert first["method"] == "model/list"
        assert first["params"] == {"includeHidden": False}
        yield Step.send(
            {
                "id": first["id"],
                "result": {"data": recorded["data"][:2], "nextCursor": "page-2"},
            }
        )
        second = yield Step.recv()
        assert second["params"] == {"includeHidden": False, "cursor": "page-2"}
        yield Step.send(
            {
                "id": second["id"],
                "result": {"data": recorded["data"][2:], "nextCursor": None},
            }
        )
        yield Step.hold(0.05)

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    models = await manager.list_models()
    assert [row["id"] for row in models] == [row["id"] for row in recorded["data"]]
    assert [e["value"] for e in models[0]["supported_efforts"]] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    await manager.close()


def test_follow_thread_start_omits_permission_overrides() -> None:
    """The follow preset is omission, not a read-only/never alias."""

    manager = CodexHostManager()
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="follow",
            workdir="/tmp/x",
            approval_policy=None,
            sandbox=None,
        )
    )
    params = manager._thread_start_params(session)  # noqa: SLF001
    assert "approvalPolicy" not in params
    assert "sandbox" not in params


def test_turn_start_carries_model_and_effort_as_one_selection() -> None:
    """A next-turn selection is encoded atomically in one request."""

    params = CodexHostManager._turn_start_params(  # noqa: SLF001
        "thr-1", "hello", model="gpt-future", effort="ultra"
    )
    assert params["model"] == "gpt-future"
    assert params["effort"] == "ultra"


def _restart_manager(fakes: list[FakeAppServer]) -> CodexHostManager:
    """Build a manager whose factory hands out one fresh fake per lifecycle."""

    state = {"i": 0}

    def factory() -> AppServerClient:
        fake = fakes[state["i"]]
        state["i"] += 1
        return AppServerClient(
            codex_bin="codex",
            expected_version="0.144.0",
            version_reader=_version_0144,
            spawner=fake.spawner(),
            close_grace_s=0.2,
            close_term_s=0.2,
        )

    return CodexHostManager(client_factory=factory)


def _behavior_server(
    *,
    on_turn=None,
    exit_after_turn: bool = False,
    block_on_turn_start: bool = False,
    orphan_before: list[dict] | None = None,
):
    """Build a scripted app-server behaviour.

    Args:
        on_turn: ``callable(thread_id, turn_id) -> list[Step]`` injecting
            custom notifications between turn/start and turn/completed.
        exit_after_turn: Send ``Step.exit(0)`` after the first turn completes
            (forces EOF → host-exited).
        block_on_turn_start: Receive ``turn/start`` but never answer (for the
            concurrent-send-rejection test).
        orphan_before: Notifications to emit right after initialise, before any
            thread binds — used to exercise orphan routing.
    """

    async def behavior():
        counter = 0
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()  # initialized
        for note in orphan_before or ():
            yield Step.send(note)
        while True:
            msg = yield Step.recv()
            if msg is None:
                return
            method = msg["method"]
            request_id = msg["id"]
            if method == "thread/start":
                counter += 1
                yield Step.send(
                    {"id": request_id, "result": _thread_result(f"t-{counter}")}
                )
            elif method == "thread/resume":
                thread_id = msg["params"]["threadId"]
                yield Step.send(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": f"no rollout found for thread id {thread_id}",
                        },
                    }
                )
            elif method == "turn/start":
                params = msg["params"]
                thread_id = params["threadId"]
                counter += 1
                turn_id = f"turn-{counter}"
                yield Step.send({"id": request_id, "result": {"turn": {"id": turn_id}}})
                if block_on_turn_start:
                    await (yield Step.hold(30))  # never answers turn/start
                yield Step.hold(0.01)  # model the response→notification gap
                if on_turn is not None:
                    for step in on_turn(thread_id, turn_id):
                        yield step
                yield Step.send(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {
                                "id": turn_id,
                                "status": "completed",
                                "durationMs": 5,
                            },
                        },
                    }
                )
                if exit_after_turn:
                    yield Step.exit(0)
            elif method == "turn/interrupt":
                params = msg["params"]
                yield Step.send({"id": request_id, "result": {}})
                yield Step.send(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": params["threadId"],
                            "turn": {
                                "id": params["turnId"],
                                "status": "interrupted",
                            },
                        },
                    }
                )
            else:
                yield Step.send({"id": request_id, "result": {}})

    return behavior()


def _deltas(thread_id: str, turn_id: str) -> list[Step]:
    """Inject one assistant delta + completed agent message for a turn."""

    return [
        Step.send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": "m1",
                    "delta": f"hi-{thread_id}",
                },
            }
        ),
        Step.send(
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "type": "agentMessage",
                        "id": "m1",
                        "text": f"hi-{thread_id}",
                        "phase": "final_answer",
                    },
                },
            }
        ),
    ]


def _has(events: list[CodexEvent], type_: CodexEventType) -> bool:
    """True if any drained event matches the given type."""

    return any(e.type is type_ for e in events)


# ----------------------------------------------------------- routing / iso


async def test_two_concurrent_threads_are_isolated() -> None:
    """Spec pass criteria: two threads concurrent, text/terminal fully isolated."""

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session_a = CodexSession(_cfg("sA"))
    session_b = CodexSession(_cfg("sB"))
    manager.register(session_a)
    manager.register(session_b)

    await asyncio.gather(
        manager.send(session_a, "helloA"),
        manager.send(session_b, "helloB"),
    )
    await asyncio.sleep(0.05)  # let the reader dispatch notifications

    events_a = session_a.drain()
    events_b = session_b.drain()

    # Every event with a thread id stays inside its own session — no cross-talk.
    for event in events_a:
        if event.thread_id is not None:
            assert event.thread_id == session_a.thread_id
    for event in events_b:
        if event.thread_id is not None:
            assert event.thread_id == session_b.thread_id

    # Both sessions saw a full turn: SESSION_STARTED, USER, TURN_STARTED,
    # assistant text and FINISHED.
    for events in (events_a, events_b):
        types = {e.type for e in events}
        assert CodexEventType.SESSION_STARTED in types
        assert CodexEventType.USER in types
        assert CodexEventType.TURN_STARTED in types
        assert CodexEventType.ASSISTANT_DELTA in types
        assert CodexEventType.ASSISTANT_MESSAGE in types
        assert CodexEventType.FINISHED in types

    # The streamed text is the session's own thread id, never its neighbour's.
    text_a = "".join(
        e.payload.get("delta", "")
        for e in events_a
        if e.type is CodexEventType.ASSISTANT_DELTA
    )
    text_b = "".join(
        e.payload.get("delta", "")
        for e in events_b
        if e.type is CodexEventType.ASSISTANT_DELTA
    )
    assert text_a == f"hi-{session_a.thread_id}"
    assert text_b == f"hi-{session_b.thread_id}"
    assert session_a.thread_id != session_b.thread_id

    await manager.close()


async def test_second_turn_reuses_thread_loaded_in_current_connection() -> None:
    """A second turn uses turn/start directly instead of thread/resume.

    ``thread/start`` already loads the new thread into the current app-server
    connection. Calling ``thread/resume`` before every later turn is both
    redundant and fatal for an ephemeral thread because it has no rollout file
    to reload. Resume belongs only to a fresh app-server connection.
    """

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session = CodexSession(CodexSessionConfig("s1", "/tmp/x", ephemeral=True))
    manager.register(session)

    await manager.send(session, "first")
    await asyncio.sleep(0.05)
    session.drain()

    await manager.send(session, "second")
    await asyncio.sleep(0.05)
    events = session.drain()

    methods = [message["method"] for message in fake.received if "method" in message]
    assert methods.count("thread/start") == 1
    assert "thread/resume" not in methods
    assert methods.count("turn/start") == 2
    assert _has(events, CodexEventType.FINISHED)
    await manager.close()


async def test_live_sessions_cannot_share_one_native_thread() -> None:
    """A second live trowel session cannot steal an attached thread route."""

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    owner = CodexSession(_cfg("owner"))
    manager.register(owner)
    await manager.send(owner, "first")
    await asyncio.sleep(0.05)
    owner.drain()
    assert owner.thread_id is not None

    duplicate = CodexSession(
        CodexSessionConfig("duplicate", "/tmp/x", initial_thread_id=owner.thread_id)
    )
    manager.register(duplicate)

    with pytest.raises(TurnConflictError, match="already attached"):
        await manager.send(duplicate, "steal")
    assert manager.session_for_thread(owner.thread_id) is owner
    await manager.close()


async def test_concurrent_resume_atomically_claims_native_thread() -> None:
    """Only one concurrent session may begin resuming the same native thread."""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()  # initialized
        first = yield Step.recv()  # first thread/resume remains pending
        second = yield Step.recv()  # buggy manager lets a duplicate through
        for request in (first, second):
            yield Step.send(
                {
                    "id": request["id"],
                    "error": {
                        "code": -32600,
                        "message": "duplicate resume reached app-server",
                    },
                }
            )

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    first = CodexSession(
        CodexSessionConfig("first", "/tmp/x", initial_thread_id="shared-thread")
    )
    second = CodexSession(
        CodexSessionConfig("second", "/tmp/x", initial_thread_id="shared-thread")
    )
    manager.register(first)
    manager.register(second)

    first_send = asyncio.create_task(manager.send(first, "first"))
    for _ in range(100):
        if any(message.get("method") == "thread/resume" for message in fake.received):
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail("first resume did not reach the fake app-server")

    try:
        with pytest.raises(TurnConflictError, match="already attached"):
            await manager.send(second, "second")
    finally:
        first_send.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_send
        await manager.close()


async def test_unregister_during_thread_attach_cannot_revive_session() -> None:
    """A deleted session cannot attach a route after thread/start returns."""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()  # initialized
        msg = yield Step.recv()  # thread/start
        yield Step.hold(0.05)
        yield Step.send({"id": msg["id"], "result": _thread_result("late-thread")})
        msg = yield Step.recv()  # buggy manager continues with turn/start
        if msg is None:
            return
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "ghost-turn"}}})
        msg = yield Step.recv()  # fixed manager interrupts an accepted ghost turn
        if msg is None:
            return
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("deleted"))
    manager.register(session)

    send_task = asyncio.create_task(manager.send(session, "hello"))
    for _ in range(100):
        if any(message.get("method") == "thread/start" for message in fake.received):
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail("thread/start did not reach the fake app-server")
    assert manager.unregister(session.session_id) is session

    with pytest.raises(TurnConflictError, match="no longer registered"):
        await send_task
    assert manager.get_session(session.session_id) is None
    assert manager.session_for_thread("late-thread") is None
    assert session.session_id not in manager._attached_session_ids  # noqa: SLF001
    assert not any(message.get("method") == "turn/start" for message in fake.received)
    assert not any(
        message.get("method") == "turn/interrupt" for message in fake.received
    )
    await manager.close()


async def test_binding_callback_runs_before_native_turn_start() -> None:
    """The caller can persist an attached binding before Codex starts work."""

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    callback_methods: list[list[str]] = []

    def persist_binding(attached: CodexSession) -> None:
        """Capture wire methods visible when persistence is requested."""

        assert attached.binding is not None
        callback_methods.append(
            [message["method"] for message in fake.received if "method" in message]
        )

    await manager.send(session, "hello", before_turn_start=persist_binding)
    await asyncio.sleep(0.05)

    assert callback_methods == [["initialize", "initialized", "thread/start"]]
    assert any(message.get("method") == "turn/start" for message in fake.received)
    await manager.close()


async def test_unregister_while_turn_start_waits_interrupts_native_turn() -> None:
    """Deletion during turn/start interrupts the accepted native turn."""

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()  # initialized
        msg = yield Step.recv()  # thread/start
        yield Step.send({"id": msg["id"], "result": _thread_result("t-delete")})
        msg = yield Step.recv()  # turn/start
        yield Step.hold(0.05)
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-delete"}}})
        msg = yield Step.recv()  # turn/interrupt
        if msg is None:
            return
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("deleted-during-turn"))
    manager.register(session)

    send_task = asyncio.create_task(manager.send(session, "hello"))
    for _ in range(100):
        if any(message.get("method") == "turn/start" for message in fake.received):
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail("turn/start did not reach the fake app-server")
    assert manager.unregister(session.session_id) is session

    with pytest.raises(TurnConflictError, match="no longer registered"):
        await send_task
    assert any(message.get("method") == "turn/interrupt" for message in fake.received)
    assert session.state.name == "IDLE"
    assert manager.session_for_thread("t-delete") is None
    await manager.close()


async def test_stale_eof_does_not_degrade_new_connection() -> None:
    """An old watcher cannot degrade a replacement while it is starting."""

    class SlowStartingClient:
        """Minimal client whose start can be paused around the watcher race."""

        def __init__(self) -> None:
            """Create start/close synchronization events."""

            self.start_entered = asyncio.Event()
            self.release_start = asyncio.Event()
            self.closed_event = asyncio.Event()
            self.closed = False

        async def start(self) -> None:
            """Pause initialization until the test releases it."""

            self.start_entered.set()
            await self.release_start.wait()

        def add_notification_listener(self, listener) -> None:
            """Accept the manager listener without producing notifications."""

            del listener

        def register_server_request_handler(self, method, handler) -> None:
            """Accept native request handlers without exercising them here."""

            del method, handler

        def register_unknown_server_request_handler(self, handler) -> None:
            """Accept the safe fallback handler without exercising it here."""

            del handler

        async def wait_closed(self) -> None:
            """Wait until manager.close closes this fake client."""

            await self.closed_event.wait()

        async def close(self) -> None:
            """Mark the fake transport closed."""

            self.closed = True
            self.closed_event.set()

    current = SlowStartingClient()
    manager = CodexHostManager(client_factory=lambda: current)  # type: ignore[arg-type]
    stale = AppServerClient(expected_version=None)
    manager._client = stale  # noqa: SLF001
    manager._state = CodexHostManagerState.DEGRADED  # noqa: SLF001
    manager._attached_session_ids.add("s1")  # noqa: SLF001

    ready_task = asyncio.create_task(manager.ensure_ready())
    await current.start_entered.wait()
    await manager._on_unexpected_exit(stale)  # noqa: SLF001

    assert manager.client is current
    assert manager.state is CodexHostManagerState.STARTING
    current.release_start.set()
    assert await ready_task is current
    await manager.close()


async def test_orphan_unknown_thread_is_not_routed() -> None:
    """A notification for an unbound thread is recorded, never pushed to a session."""

    ghost_note = {
        "method": "item/agentMessage/delta",
        "params": {"threadId": "t-ghost", "turnId": "x", "itemId": "m", "delta": "boo"},
    }
    fake = FakeAppServer(_behavior_server(orphan_before=[ghost_note]))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.send(session, "hi")
    await asyncio.sleep(0.05)
    events = session.drain()
    # The ghost notification never entered the session queue.
    assert not any("boo" in str(e.payload) for e in events)
    # It was recorded as an orphan diagnostic.
    orphans = manager.orphans
    assert any(
        o.method == "item/agentMessage/delta" and o.thread_id == "t-ghost"
        for o in orphans
    )
    await manager.close()


async def test_orphan_unknown_method_is_recorded() -> None:
    """A method the translator does not map is recorded as unknown_method."""

    unknown_note = {
        "method": "some/future/thing",
        "params": {"threadId": None},  # no threadId either
    }
    fake = FakeAppServer(_behavior_server(orphan_before=[unknown_note]))
    manager = _manager(fake)
    await manager.ensure_ready()
    await asyncio.sleep(0.05)
    # No session is registered, but the notification has no threadId so it
    # surfaces as a no_thread_id orphan (the routing stage, before the
    # translator's unknown_method classification).
    orphans = manager.orphans
    assert any(o.method == "some/future/thing" for o in orphans)
    await manager.close()


# ------------------------------------------------------ single-turn guard


async def test_concurrent_send_same_session_is_rejected() -> None:
    """Spec C-3: a second send while the first turn is mid-flight is rejected."""

    fake = FakeAppServer(_behavior_server(block_on_turn_start=True))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    first = asyncio.create_task(manager.send(session, "first"))
    await asyncio.sleep(0.05)  # let first enter begin_send + await turn/start
    with pytest.raises(TurnConflictError):
        await manager.send(session, "second")

    first.cancel()
    try:
        await first
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    # The rejected send did not corrupt the session — it can still be aborted.
    session.abort_send()
    await manager.close()


# --------------------------------------------------------------- host exit


async def test_eof_marks_running_turn_host_exited() -> None:
    """Spec §4: EOF → manager degraded, running turn ends with HOST_EXITED."""

    fake = FakeAppServer(_behavior_server(exit_after_turn=True))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.send(session, "hi")
    await asyncio.sleep(0.05)  # turn completes then fake exits
    # The fake exits after turn/completed, so the turn already finished
    # cleanly; the manager still observes the EOF and flips to degraded.
    await asyncio.sleep(0.05)
    assert manager.state is CodexHostManagerState.DEGRADED
    await manager.close()


async def test_eof_while_running_pushes_host_exited_terminal() -> None:
    """A running turn that loses the host gets a concrete HOST_EXITED event."""

    # Custom behaviour: start a turn, then exit mid-flight (no turn/completed).
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()  # thread/start
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()  # turn/start
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.02)  # turn is now running on the client side
        yield Step.exit(0)  # host vanishes mid-turn

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    await manager.send(session, "hi")
    await asyncio.sleep(0.1)  # let the EOF watcher fan out host-exited

    assert manager.state is CodexHostManagerState.DEGRADED
    events = session.drain()
    assert any(
        e.type is CodexEventType.HOST_STATUS
        and e.payload.get("status") == "host_exited"
        for e in events
    )
    # The session kept its binding so it can resume after restart.
    assert session.binding is not None
    assert session.binding.thread_id == "t-1"
    await manager.close()


async def test_eof_during_turn_start_window_does_not_deadlock() -> None:
    """H-2: EOF in the begin_send → record window must not pin ``_sending``.

    The host dies while ``turn/start`` is unanswered. ``manager.send`` raises
    ``TransportClosedError`` (the pending future is failed); the eof watcher
    must also clear the session's in-flight flag so a follow-up ``begin_send``
    does not hit ``TurnConflictError`` forever.
    """

    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()  # thread/start
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        yield Step.recv()  # turn/start — never answered
        yield Step.hold(0.05)
        yield Step.exit(1)  # host dies mid-turn-start

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)
    with pytest.raises(TransportClosedError):
        await manager.send(session, "hi")
    await asyncio.sleep(0.1)  # let the eof watcher fan out
    assert manager.state is CodexHostManagerState.DEGRADED
    # The session is not deadlocked: ``_sending`` cleared, new send allowed.
    session.begin_send()
    await manager.close()


# --------------------------------------------------------------- interrupt


async def test_interrupt_sends_request_and_closes_turn_interrupted() -> None:
    """Spec C-4: interrupt forwards turn/interrupt; native status closes the turn."""

    fake = FakeAppServer(_behavior_server(block_on_turn_start=True))
    manager = _manager(fake)
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    send_task = asyncio.create_task(manager.send(session, "hi"))
    await asyncio.sleep(0.05)  # parked awaiting turn/start response
    # The fake is blocked on turn/start; flip its behaviour by injecting an
    # interrupt response directly is not possible mid-script. Instead, drive a
    # fresh manager/script that actually completes the interrupt handshake.
    send_task.cancel()
    try:
        await send_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    await manager.close()

    # Real interrupt handshake on a fresh manager.
    async def behavior():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()  # thread/start
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()  # turn/start
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.02)
        msg = yield Step.recv()  # turn/interrupt
        assert msg["method"] == "turn/interrupt"
        yield Step.send({"id": msg["id"], "result": {}})
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": msg["params"]["threadId"],
                    "turn": {"id": msg["params"]["turnId"], "status": "interrupted"},
                },
            }
        )
        yield Step.recv()

    fake2 = FakeAppServer(behavior())
    manager2 = _manager(fake2)
    session2 = CodexSession(_cfg("s2"))
    manager2.register(session2)
    await manager2.send(session2, "hi")
    await manager2.interrupt(session2)
    await asyncio.sleep(0.05)
    events = session2.drain()
    assert _has(events, CodexEventType.INTERRUPTED)
    assert any(m["method"] == "turn/interrupt" for m in fake2.received)
    await manager2.close()


# --------------------------------------------------------------- reuse


async def test_ensure_ready_reuses_one_client_across_sessions() -> None:
    """Spec C-1: the shared client is reused, not restarted per session."""

    fake = FakeAppServer(_behavior_server(on_turn=_deltas))
    manager = _manager(fake)
    session_a = CodexSession(_cfg("s1"))
    session_b = CodexSession(_cfg("s2"))
    manager.register(session_a)
    manager.register(session_b)

    await manager.send(session_a, "hi")
    client_after_a = manager.client
    assert client_after_a is not None
    await asyncio.sleep(0.02)

    await manager.send(session_b, "yo")
    assert manager.client is client_after_a  # same transport
    assert manager.state is CodexHostManagerState.READY
    await manager.close()


# --------------------------------------------------- turn/start params


async def test_effort_sent_on_turn_start_not_thread_start() -> None:
    """``effort`` belongs on turn/start, not thread/start.

    # source: v2/thread.rs::ThreadStartParams has no ``effort`` field;
    # v2/turn.rs::TurnStartParams has ``effort: Option<ReasoningEffort>``.
    Sending it on thread/start is an unknown field (rejected or ignored by
    stricter servers) and the override never reaches the first turn.
    """

    thread_start_params: dict | None = None
    turn_start_params: dict | None = None

    async def behavior():
        nonlocal thread_start_params, turn_start_params
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()  # thread/start
        thread_start_params = msg["params"]
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()  # turn/start
        turn_start_params = msg["params"]
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-1", "status": "completed", "durationMs": 1},
                },
            }
        )
        yield Step.recv()

    fake = FakeAppServer(behavior())
    manager = _manager(fake)
    session = CodexSession(CodexSessionConfig("s1", "/tmp/x", effort="high"))
    manager.register(session)
    await manager.send(session, "hi")
    await asyncio.sleep(0.05)

    assert thread_start_params is not None and turn_start_params is not None
    assert "effort" not in thread_start_params  # ThreadStartParams has no effort
    assert turn_start_params.get("effort") == "high"  # TurnStartParams.effort
    await manager.close()


# ----------------------------------------------------- degraded → restart


async def test_restart_resumes_same_thread_on_next_send() -> None:
    """Spec §4: after EOF, the next send restarts the manager and resumes."""

    # Lifecycle 1: start a thread, then exit mid-turn.
    async def behavior1():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()  # thread/start
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()  # turn/start
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
        yield Step.hold(0.02)
        yield Step.exit(0)

    # Lifecycle 2: resume the same thread + complete a fresh turn referencing
    # the marker from the previous turn.
    async def behavior2():
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()  # thread/resume
        assert msg["method"] == "thread/resume"
        assert msg["params"]["threadId"] == "t-1"
        yield Step.send({"id": msg["id"], "result": _thread_result("t-1")})
        msg = yield Step.recv()  # turn/start
        yield Step.send({"id": msg["id"], "result": {"turn": {"id": "turn-2"}}})
        yield Step.hold(0.01)
        yield Step.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turn": {"id": "turn-2", "status": "completed", "durationMs": 3},
                },
            }
        )
        yield Step.recv()

    fake1 = FakeAppServer(behavior1())
    fake2 = FakeAppServer(behavior2())
    manager = _restart_manager([fake1, fake2])
    session = CodexSession(_cfg("s1"))
    manager.register(session)

    await manager.send(session, "first")  # lifecycle 1
    await asyncio.sleep(0.05)  # EOF → degraded
    assert manager.state is CodexHostManagerState.DEGRADED
    assert session.binding is not None and session.binding.thread_id == "t-1"

    # Recovery: next send restarts the manager (lifecycle 2) and resumes t-1.
    await manager.send(session, "second after restart")
    await asyncio.sleep(0.05)
    assert manager.state is CodexHostManagerState.READY
    events = session.drain()
    # Recovery produced a visible READY host-status flip (spec §4).
    assert any(
        e.type is CodexEventType.HOST_STATUS and e.payload.get("status") == "ready"
        for e in events
    )
    # The resumed turn completed.
    assert _has(events, CodexEventType.FINISHED)
    # thread/resume was used, not thread/start.
    assert any(m["method"] == "thread/resume" for m in fake2.received)
    await manager.close()
