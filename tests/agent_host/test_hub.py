"""slice-072: SessionHub — host-neutral create / route / freeze invariants.

The Hub owns the :class:`BindingStore` (persistence) and routes create / send
/ interrupt / resume to the right native host (CCHost via cc_host's registry,
or the shared CodexHostManager). Tests inject fakes for both sides so the
routing logic is exercised deterministically without spawning any real
subprocess (spec C-7).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from fastapi import HTTPException

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import (
    CrossRuntimeResumeError,
    RuntimeFrozenError,
    SessionHub,
)
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.agent_host.store import BindingStore
from trowel_py.cc_host.routes import OpenedCcSession


# ---------------------------------------------------------------------------
# Fakes — duck-typed stands for CCHost and the CodexHostManager.
# ---------------------------------------------------------------------------


class FakeCcHost:
    """Minimal CCHost stand-in: the Hub only reads a few attrs + send/close."""

    def __init__(self, workdir: str, model: str = "glm-5.2") -> None:
        self.workdir = workdir
        self.model = model
        self.running = False
        self.is_dead = False
        self.memory_enabled = True
        self.profile_enabled = True
        self.closed = False
        self.interrupted = False
        self.cc_session_id: str | None = None

    async def send(self, text: str) -> AsyncIterator[dict[str, Any]]:
        self.running = True
        yield {"type": "text", "text": f"echo:{text}"}
        self.running = False

    async def interrupt(self) -> None:
        self.interrupted = True

    async def close(self) -> None:
        self.closed = True


class FakeCodexManager:
    """Duck-typed stand-in for the CodexHostManager surface the Hub uses."""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.sent: list[tuple[str, str]] = []
        self.interrupted: list[str] = []
        self.models: list[dict[str, Any]] = [
            {
                "id": "gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "display_name": "Sol",
                "description": "",
                "is_default": True,
                "default_effort": "low",
                "supported_efforts": [
                    {"value": "low", "description": ""},
                    {"value": "ultra", "description": ""},
                ],
            },
            {
                "id": "gpt-5.6-luna",
                "model": "gpt-5.6-luna",
                "display_name": "Luna",
                "description": "",
                "is_default": False,
                "default_effort": "medium",
                "supported_efforts": [
                    {"value": "low", "description": ""},
                    {"value": "medium", "description": ""},
                ],
            },
        ]

    def register(self, session: Any) -> None:
        self.sessions[session.session_id] = session

    def get_session(self, sid: str) -> Any | None:
        return self.sessions.get(sid)

    @property
    def session_ids(self) -> tuple[str, ...]:
        """Mirror CodexHostManager.session_ids (review CRITICAL-1 alignment)."""

        return tuple(self.sessions.keys())

    def unregister(self, sid: str) -> Any | None:
        """Mirror CodexHostManager.unregister."""

        return self.sessions.pop(sid, None)

    async def send(
        self,
        session: Any,
        text: str,
        *,
        before_turn_start=None,
    ) -> str:
        """Record the send and run the manager's pre-turn callback when present."""

        self.sent.append((session.session_id, text))
        if before_turn_start is not None:
            before_turn_start(session)
        return "fake-turn-id"

    async def interrupt(self, session: Any) -> None:
        self.interrupted.append(session.session_id)

    async def list_models(self) -> list[dict[str, Any]]:
        """Return the fake catalog used by Agent route tests."""

        return self.models


class _FakeThreadBinding:
    """Stand-in for the Codex thread binding the writeback reads."""

    def __init__(self, thread_id: str, model: str) -> None:
        self.thread_id = thread_id
        self.model = model


class FakeCodexSession:
    """Duck-typed stand-in for a CodexSession the Hub streams off.

    ``events()`` replays a pre-seeded list of CodexEvents (real dataclass
    instances, so the adapter under test sees the exact shape the manager
    emits).
    """

    def __init__(
        self,
        session_id: str,
        events: list[Any],
        *,
        thread_id: str = "thr-1",
        model: str = "gpt-5.6-sol",
    ) -> None:
        self.session_id = session_id
        self._events = list(events)
        self.binding = _FakeThreadBinding(thread_id, model)
        self.state = "idle"

    async def events(self) -> AsyncIterator[Any]:
        for ev in self._events:
            yield ev


def make_cc_opener(registry: dict[str, FakeCcHost], name_counts: dict[str, int]):
    """Build a cc_opener that constructs a FakeCcHost, not a real CCHost."""

    def opener(
        req: CreateAgentSessionRequest, request: Any, reg: dict[str, Any] | None = None
    ) -> OpenedCcSession:
        del request  # the fake does not read app.state
        sid = "cc-" + uuid.uuid4().hex[:8]
        host = FakeCcHost(req.workdir, model=req.model or "glm-5.2")
        target = reg if reg is not None else registry
        target[sid] = host
        registry[sid] = host
        basename = Path(req.workdir).name or str(req.workdir)
        n = name_counts.get(basename, 0)
        name_counts[basename] = n + 1
        name = basename if n == 0 else f"{basename} #{n + 1}"
        return OpenedCcSession(sid=sid, host=host, name=name)

    return opener


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cc_registry() -> dict[str, FakeCcHost]:
    return {}


@pytest.fixture
def name_counts() -> dict[str, int]:
    return {}


@pytest.fixture
def codex_mgr() -> FakeCodexManager:
    return FakeCodexManager()


@pytest.fixture
def hub(
    tmp_path: Path,
    cc_registry: dict[str, FakeCcHost],
    name_counts: dict[str, int],
    codex_mgr: FakeCodexManager,
) -> SessionHub:
    store = BindingStore(tmp_path / "agent_sessions.json")
    return SessionHub(
        store,
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


def cc_req(workdir: Path, **over: Any) -> CreateAgentSessionRequest:
    base: dict[str, Any] = dict(runtime="claude_code", workdir=str(workdir))
    base.update(over)
    return CreateAgentSessionRequest(**base)


def codex_req(workdir: Path, **over: Any) -> CreateAgentSessionRequest:
    base: dict[str, Any] = dict(runtime="codex", workdir=str(workdir))
    base.update(over)
    return CreateAgentSessionRequest(**base)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_cc_session_creates_binding_and_registry_host(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir), request=None)
    assert binding.runtime is Runtime.CLAUDE_CODE
    assert binding.workdir == str(workdir)
    assert binding.session_id in cc_registry
    assert cc_registry[binding.session_id].workdir == str(workdir)
    assert hub.get(binding.session_id) is not None


def test_create_codex_session_creates_binding_and_registers_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir), request=None)
    assert binding.runtime is Runtime.CODEX
    assert binding.session_id in codex_mgr.sessions
    assert hub.get(binding.session_id) is not None


def test_create_codex_follow_does_not_invent_overrides(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """Follow leaves both native permission overrides unset."""

    binding = hub.create(codex_req(workdir, permission_preset="follow"), request=None)
    session = codex_mgr.get_session(binding.session_id)
    assert session.config.approval_policy is None
    assert session.config.sandbox is None
    assert binding.permission_preset == "follow"
    assert binding.permission is None


@pytest.mark.parametrize(
    ("preset", "approval", "sandbox"),
    [
        ("read-only", "on-request", "read-only"),
        ("workspace-write", "on-request", "workspace-write"),
        ("danger-full-access", "never", "danger-full-access"),
    ],
)
def test_create_codex_permission_presets_are_centralized(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    preset: str,
    approval: str,
    sandbox: str,
):
    """The backend, not the browser, owns preset-to-native parameter mapping."""

    binding = hub.create(codex_req(workdir, permission_preset=preset), request=None)
    config = codex_mgr.get_session(binding.session_id).config
    assert (config.approval_policy, config.sandbox) == (approval, sandbox)


def test_create_cc_with_resume_carries_native_id(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir, resume_from="cc-jsonl-9"), request=None)
    assert binding.native_session_id == "cc-jsonl-9"


def test_create_codex_with_resume_seeds_thread_binding(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """resume_from seeds the Codex session so the first send resumes (HIGH-4).

    Without the seed, CodexSession.is_new_thread stays True and manager.send
    would call thread/start (a new conversation) instead of thread/resume.
    """

    binding = hub.create(
        codex_req(workdir, resume_from="codex-thread-abc"), request=None
    )
    session = codex_mgr.get_session(binding.session_id)
    assert session is not None
    assert session.config.initial_thread_id == "codex-thread-abc"
    assert session.binding is not None
    assert session.binding.thread_id == "codex-thread-abc"
    assert session.is_new_thread is False


async def test_delete_codex_unregisters_from_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """delete() routes through manager.unregister, not a private attr poke."""

    binding = hub.create(codex_req(workdir), request=None)
    sid = binding.session_id
    assert sid in codex_mgr.sessions
    assert await hub.delete(sid) is True
    assert sid not in codex_mgr.sessions


async def test_delete_cc_clears_cc_multiopen_state(
    hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """delete() on a CC session clears cc_host multi-open bookkeeping (HIGH-1).

    The fake cc_opener does not touch cc_host's module globals (only the real
    open_cc_session does), so we isolate those globals with monkeypatch and
    pre-seed them to mirror the real opener's side effects, then assert the
    closer clears them.
    """

    from trowel_py.cc_host import routes as cc_routes

    monkeypatch.setattr(cc_routes, "_WORKDIR_INDEX", {})
    monkeypatch.setattr(cc_routes, "_SESSION_NAMES", {})
    binding = hub.create(cc_req(workdir), request=None)
    sid = binding.session_id
    cc_routes._WORKDIR_INDEX.setdefault(str(workdir), set()).add(sid)
    cc_routes._SESSION_NAMES[sid] = "proj"
    await hub.delete(sid)
    assert sid not in cc_routes._SESSION_NAMES
    assert sid not in cc_routes._WORKDIR_INDEX.get(str(workdir), set())


def test_activate_cc_mirrors_legacy_active_sid(
    hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Activating a CC session mirrors cc_host._ACTIVE_SID (HIGH-3)."""

    from trowel_py.cc_host import routes as cc_routes

    monkeypatch.setattr(cc_routes, "_ACTIVE_SID", None)
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    hub.activate(cc.session_id)
    assert cc_routes._ACTIVE_SID == cc.session_id
    # switching to the Codex session does not touch the legacy CC active id
    hub.activate(cx.session_id)
    assert cc_routes._ACTIVE_SID == cc.session_id


def test_create_missing_workdir_400(hub: SessionHub):
    with pytest.raises(HTTPException) as exc:
        hub.create(cc_req(Path("/nonexistent/xyz-123")), request=None)
    assert exc.value.status_code == 400


def test_create_connection_cap_409(hub: SessionHub, workdir: Path, monkeypatch):
    monkeypatch.setattr("trowel_py.agent_host.hub.MAX_CONNECTIONS", 1)
    hub.create(cc_req(workdir), request=None)
    with pytest.raises(HTTPException) as exc:
        hub.create(codex_req(workdir), request=None)
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# list_active (mixed)
# ---------------------------------------------------------------------------


def test_list_active_mixes_cc_and_codex(hub: SessionHub, workdir: Path):
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    sessions, active_id = hub.list_active()
    ids = {s["session_id"] for s in sessions}
    runtimes = {s["runtime"] for s in sessions}
    assert ids == {cc.session_id, cx.session_id}
    assert runtimes == {"claude_code", "codex"}
    assert active_id == cx.session_id  # last-created is active


# ---------------------------------------------------------------------------
# runtime frozen (C-1) + cross-resume forbidden (C-2)
# ---------------------------------------------------------------------------


def test_patch_runtime_change_rejected(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir), request=None)
    with pytest.raises(RuntimeFrozenError):
        hub.patch(binding.session_id, runtime="codex")


def test_patch_same_runtime_ok(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir), request=None)
    hub.patch(binding.session_id, runtime="claude_code")  # no error


async def test_codex_model_switch_queues_valid_pair_and_auto_falls_back(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """Sol/ultra -> Luna becomes Luna/medium before the next turn."""

    binding = hub.create(
        codex_req(workdir, model="gpt-5.6-sol", effort="ultra"), request=None
    )
    selected = await hub.update_codex_settings(
        binding.session_id, model="gpt-5.6-luna", effort="ultra"
    )
    assert selected == {
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "adjusted": True,
    }
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_settings() == ("gpt-5.6-luna", "medium")
    # Public binding remains effective, not the unaccepted pending selection.
    assert hub.get(binding.session_id).model == "gpt-5.6-sol"


async def test_codex_model_switch_rejects_unknown_model(hub: SessionHub, workdir: Path):
    """The backend never sends a combination absent from the native catalog."""

    binding = hub.create(codex_req(workdir), request=None)
    with pytest.raises(HTTPException) as exc:
        await hub.update_codex_settings(
            binding.session_id, model="not-in-catalog", effort="low"
        )
    assert exc.value.status_code == 422


async def test_codex_effort_only_uses_native_default_model_for_fresh_session(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """A follow-mode session without a current model still stages a full pair."""

    binding = hub.create(codex_req(workdir, model=None, effort=None), request=None)
    selected = await hub.update_codex_settings(
        binding.session_id, model=None, effort="ultra"
    )
    assert selected == {
        "model": "gpt-5.6-sol",
        "effort": "ultra",
        "adjusted": False,
    }
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_settings() == ("gpt-5.6-sol", "ultra")


def test_validate_resume_rejects_cross_runtime(hub: SessionHub, workdir: Path):
    """A native id already bound to CC cannot be resumed as Codex (C-2)."""

    hub._store.put(
        make_binding(
            session_id="old-cc",
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="cc-jsonl-1",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )
    with pytest.raises(CrossRuntimeResumeError):
        hub.validate_resume(Runtime.CODEX, "cc-jsonl-1")


def test_validate_resume_same_runtime_ok(hub: SessionHub):
    hub.validate_resume(Runtime.CLAUDE_CODE, "fresh-cc-id")  # no conflict
    hub.validate_resume(Runtime.CODEX, None)  # fresh codex thread


# ---------------------------------------------------------------------------
# activate / delete / restart
# ---------------------------------------------------------------------------


def test_activate_sets_active_id(hub: SessionHub, workdir: Path):
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    hub.activate(cc.session_id)
    sessions, active_id = hub.list_active()
    assert active_id == cc.session_id
    # C-4: switching the active view does not drop the other session.
    assert cx.session_id in {s["session_id"] for s in sessions}


async def test_delete_cc_closes_host_and_drops_binding(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir), request=None)
    sid = binding.session_id
    host = cc_registry[sid]
    assert await hub.delete(sid) is True
    assert hub.get(sid) is None
    assert sid not in cc_registry
    assert host.closed is True


async def test_delete_codex_drops_binding(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir), request=None)
    sid = binding.session_id
    assert await hub.delete(sid) is True
    assert hub.get(sid) is None
    assert sid not in codex_mgr.sessions


async def test_delete_unknown_returns_false(hub: SessionHub):
    assert await hub.delete("nope") is False


def test_restart_recovers_bindings_from_store(
    hub: SessionHub, workdir: Path, tmp_path: Path
):
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    # simulate a trowel restart: brand-new Hub on the same store file
    restarted = SessionHub(
        BindingStore(hub._store.path),
        codex_manager=FakeCodexManager(),
        cc_registry={},
        cc_opener=make_cc_opener({}, {}),
    )
    bindings = {b.session_id: b for b in restarted._store.list_all()}
    assert cc.session_id in bindings
    assert cx.session_id in bindings
    assert bindings[cc.session_id].runtime is Runtime.CLAUDE_CODE
    assert bindings[cx.session_id].runtime is Runtime.CODEX


# ---------------------------------------------------------------------------
# interrupt routing
# ---------------------------------------------------------------------------


async def test_interrupt_routes_to_cc(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir), request=None)
    await hub.interrupt(binding.session_id)
    assert cc_registry[binding.session_id].interrupted is True


async def test_interrupt_routes_to_codex(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir), request=None)
    await hub.interrupt(binding.session_id)
    assert binding.session_id in codex_mgr.interrupted


async def test_interrupt_unknown_session_404(hub: SessionHub):
    with pytest.raises(HTTPException) as exc:
        await hub.interrupt("nope")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# CC stream (Codex stream is exercised by the integration smoke)
# ---------------------------------------------------------------------------


async def test_stream_unknown_session_404(hub: SessionHub):
    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream("nope", "hi")]
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# slice-074: unified AgentEvent v1 envelope on both runtimes' streams
# ---------------------------------------------------------------------------

from trowel_py.codex_host.events import (  # noqa: E402 — local after fakes
    CodexEvent,
    CodexEventType,
    immutable_payload,
)
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA  # noqa: E402


def _is_envelope(e: dict) -> bool:
    """Every event the hub emits after slice-074 carries the v1 schema stamp."""

    return e.get("schema") == AGENT_EVENT_SCHEMA


async def test_stream_cc_yields_unified_envelope(hub: SessionHub, workdir: Path):
    """CC events arrive as AgentEvent v1 with monotonic seq + claude_code tag."""

    binding = hub.create(cc_req(workdir), request=None)
    events = [e async for e in hub.stream(binding.session_id, "hello")]
    assert events, "expected at least one event from the CC stream"
    assert all(_is_envelope(e) for e in events), events
    assert all(e["runtime"] == "claude_code" for e in events)
    assert all(e["session_id"] == binding.session_id for e in events)
    # CC adapter stamps type verbatim (the FakeCcHost sends a text event)
    assert events[0]["type"] == "text"
    assert events[0]["payload"]["text"] == "echo:hello"
    # seq monotonic from 1
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


async def test_stream_cc_seq_persists_across_turns(hub: SessionHub, workdir: Path):
    """seq spans turns within a session — the adapter is not re-created per send."""

    binding = hub.create(cc_req(workdir), request=None)
    first = [e async for e in hub.stream(binding.session_id, "one")]
    second = [e async for e in hub.stream(binding.session_id, "two")]
    assert first[-1]["seq"] >= 1
    assert second[0]["seq"] == first[-1]["seq"] + 1, (
        "seq must continue from the prior turn, not reset to 1"
    )


async def test_stream_codex_yields_unified_envelope(hub: SessionHub, workdir: Path):
    """Codex events arrive as AgentEvent v1 with TrowelEvent-aligned type names."""

    binding = make_binding(
        session_id="codex-sess",
        runtime=Runtime.CODEX,
        native_session_id=None,
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="proj",
    )
    hub.store.put(binding)
    codex_events = [
        CodexEvent(
            session_id="codex-sess",
            seq=1,
            type=CodexEventType.SESSION_STARTED,
            thread_id="thr-1",
            payload=immutable_payload(model="gpt-5.6-sol", cwd=str(workdir)),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=2,
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="thr-1",
            turn_id="turn-1",
            item_id="item-1",
            payload=immutable_payload(delta="hello "),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=3,
            type=CodexEventType.FINISHED,
            thread_id="thr-1",
            turn_id="turn-1",
            payload=immutable_payload(status="completed"),
        ),
    ]
    session = FakeCodexSession("codex-sess", codex_events)
    hub._codex.register(session)  # noqa: SLF001 — wire the fake into the manager

    events = [e async for e in hub.stream("codex-sess", "hello")]
    assert all(_is_envelope(e) for e in events), events
    assert all(e["runtime"] == "codex" for e in events)
    # assistant_delta was renamed to text (unified to TrowelEvent vocabulary)
    types = [e["type"] for e in events]
    assert types == ["session_started", "text", "finished"], types
    # payload.delta was remapped to payload.text
    text_ev = next(e for e in events if e["type"] == "text")
    assert text_ev["payload"]["text"] == "hello "
    assert text_ev["item_id"] == "item-1"


async def test_stream_codex_persists_binding_when_turn_start_fails(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
):
    """A native thread remains resumable when failure follows attachment."""

    binding = hub.create(codex_req(workdir), request=None)
    session = FakeCodexSession(
        binding.session_id,
        [],
        thread_id="thr-created-before-turn-failure",
    )
    codex_mgr.sessions[binding.session_id] = session

    async def fail_after_thread_attached(
        session: Any,
        text: str,
        *,
        before_turn_start=None,
    ) -> str:
        """Model a successful thread response followed by turn/start failure."""

        del text
        if before_turn_start is not None:
            before_turn_start(session)
        raise RuntimeError("turn/start failed")

    monkeypatch.setattr(codex_mgr, "send", fail_after_thread_attached)

    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream(binding.session_id, "hello")]
    assert exc.value.status_code == 502
    persisted = hub.get(binding.session_id)
    assert persisted is not None
    assert persisted.native_session_id == "thr-created-before-turn-failure"


async def test_stream_codex_surfaces_writeback_failure_before_native_turn(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
):
    """Binding persistence failure stops the send before native work begins."""

    binding = hub.create(codex_req(workdir), request=None)
    codex_mgr.sessions[binding.session_id] = FakeCodexSession(
        binding.session_id,
        [],
        thread_id="thr-non-durable",
    )

    def fail_writeback(*args: Any, **kwargs: Any) -> None:
        """Model binding persistence failure before native work begins."""

        del args, kwargs
        raise OSError("binding store unavailable")

    monkeypatch.setattr(hub.store, "update_native", fail_writeback)

    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream(binding.session_id, "hello")]
    assert exc.value.status_code == 502
    assert "binding store unavailable" in str(exc.value.detail)


async def test_stream_codex_finished_terminates_loop(hub: SessionHub, workdir: Path):
    """A Codex terminal event breaks the stream even if the queue had more."""

    binding = make_binding(
        session_id="codex-sess",
        runtime=Runtime.CODEX,
        native_session_id=None,
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="proj",
    )
    hub.store.put(binding)
    # finished in the middle; a trailing event must NOT leak through
    codex_events = [
        CodexEvent(
            session_id="codex-sess",
            seq=1,
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="thr-1",
            turn_id="turn-1",
            item_id="item-1",
            payload=immutable_payload(delta="hi"),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=2,
            type=CodexEventType.FINISHED,
            thread_id="thr-1",
            turn_id="turn-1",
            payload=immutable_payload(status="completed"),
        ),
        CodexEvent(
            session_id="codex-sess",
            seq=3,
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="thr-1",
            turn_id="turn-1",
            item_id="item-2",
            payload=immutable_payload(delta="should not appear"),
        ),
    ]
    session = FakeCodexSession("codex-sess", codex_events)
    hub._codex.register(session)  # noqa: SLF001

    events = [e async for e in hub.stream("codex-sess", "hi")]
    types = [e["type"] for e in events]
    assert types == ["text", "finished"], types
